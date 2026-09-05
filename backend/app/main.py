"""FastAPI application entry point.

Mounts all API routers under /api/v1, sets up CORS, structured logging,
and Prometheus metrics. Entry point for both the API server (uvicorn)
and the Celery workers (via app.workers.tasks.celery_app).
"""

import asyncio
from contextlib import asynccontextmanager
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core import metrics as _metrics  # noqa: F401 — register Prometheus metrics
from app.core.metrics import QUEUE_CONSUMERS_GAUGE, QUEUE_DEPTH_GAUGE
from app.core.storage import ensure_bucket_exists
from app.db import async_session_factory
from app.api.v1.auth import router as auth_router
from app.api.v1.cvs import router as cvs_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.job_posts import router as job_posts_router
from app.api.v1.matches import router as matches_router
from app.api.v1.resume_rewrites import router as resume_rewrites_router
from app.api.v1.cover_letters import router as cover_letters_router
from app.api.v1.trial_sessions import router as trial_sessions_router
from app.api.v1.tailored_cvs import router as tailored_cvs_router
from app.api.v1.exports import router as exports_router
from app.api.v1.coverage import router as coverage_router
from app.api.v1.audit import router as audit_router
from app.api.v1.client_metrics import router as client_metrics_router
from app.api.v1.applications import router as applications_router
from app.api.v1.job_feed import router as job_feed_router

logger = get_logger(__name__)


_QUEUE_DEPTH_POLL_SECONDS = 15
_QUEUE_CONSUMER_POLL_SECONDS = 30
_QUEUE_CONSUMER_INSPECT_TIMEOUT_SECONDS = 5


async def _poll_queue_depth() -> None:
    """Keeps QUEUE_DEPTH_GAUGE current from the database (§10 QueueDepthSpike).

    Runs in the API process, which Prometheus actually scrapes — deliberately
    not a worker-side counter increment, since queue depth is a live property
    of the processing_jobs table, not an event any one worker sees. Also
    fixes a real bug this replaced: the old rule filtered on
    processing_jobs_total{status="queued"}, but that Prometheus counter is
    only ever incremented with status="completed"/"failed" — the "queued"
    label value never existed in it at all.
    """
    while True:
        try:
            async with async_session_factory() as session:
                rows = await session.execute(
                    text(
                        "SELECT job_type, count(*) FROM processing_jobs "
                        "WHERE status NOT IN ('completed', 'failed') "
                        "GROUP BY job_type"
                    )
                )
                seen = set()
                for job_type, count in rows.all():
                    QUEUE_DEPTH_GAUGE.labels(job_type=job_type).set(count)
                    seen.add(job_type)
                # Job types with zero outstanding jobs won't appear in the
                # query above — zero them explicitly so the gauge doesn't
                # keep reporting a stale nonzero value forever.
                for job_type in _KNOWN_JOB_TYPES - seen:
                    QUEUE_DEPTH_GAUGE.labels(job_type=job_type).set(0)
        except Exception as e:
            logger.warning("queue_depth_poll_failed", error=str(e))
        await asyncio.sleep(_QUEUE_DEPTH_POLL_SECONDS)


async def _poll_queue_consumers() -> None:
    """Keeps QUEUE_CONSUMERS_GAUGE current from Celery's control plane.

    Pairs with _poll_queue_depth to make "nobody is consuming this queue"
    a detectable state. Depth on its own is ambiguous — a nonzero depth
    looks identical whether workers are chewing through the backlog or
    the queue has no consumer at all, and only the second one never
    resolves on its own.

    `inspect.active_queues()` is a synchronous broadcast RPC with its own
    socket timeout, so it runs in a thread rather than blocking the event
    loop. On any failure every gauge goes to -1 ("unknown"), never 0 —
    a broker blip must not look like a missing worker, since the alert
    that reads this gauge pages on 0.
    """
    while True:
        try:
            replies = await asyncio.to_thread(_inspect_active_queues)
            if replies is None:
                # Broker reachable but no worker answered at all. That is
                # itself the condition we care about, so it is a real 0.
                replies = {}
            consumers: dict[str, int] = {job_type: 0 for job_type in _KNOWN_JOB_TYPES}
            for queues in replies.values():
                for queue in queues or []:
                    name = queue.get("name")
                    if name in consumers:
                        consumers[name] += 1
            for job_type, count in consumers.items():
                QUEUE_CONSUMERS_GAUGE.labels(job_type=job_type).set(count)
        except Exception as e:
            logger.warning("queue_consumer_poll_failed", error=str(e))
            for job_type in _KNOWN_JOB_TYPES:
                QUEUE_CONSUMERS_GAUGE.labels(job_type=job_type).set(-1)
        await asyncio.sleep(_QUEUE_CONSUMER_POLL_SECONDS)


def _inspect_active_queues():
    """Blocking Celery control-plane call, isolated for asyncio.to_thread."""
    from app.workers.tasks import celery_app

    return celery_app.control.inspect(
        timeout=_QUEUE_CONSUMER_INSPECT_TIMEOUT_SECONDS
    ).active_queues()


_KNOWN_JOB_TYPES = {
    # Steps 3-6 (docling_extract / textract_extract / merge_parse /
    # cv_parse) are decommissioned — see decommissioned/README.md.
    "text_extract",
    "ats_check",
    "job_post_fetch",
    "job_post_parse",
    "match",
    "cv_generate",
    "cover_letter_generate",
    "export",
    "export_pdf",
    "coverage_report",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: setup and teardown."""
    setup_logging()
    logger.info("app_starting", environment=settings.environment)
    # Ensure S3 bucket exists (local MinIO auto-creates)
    try:
        await ensure_bucket_exists()
    except Exception:
        logger.warning("bucket_setup_skipped", reason="storage may not be available yet")
    queue_depth_task = asyncio.create_task(_poll_queue_depth())
    queue_consumer_task = asyncio.create_task(_poll_queue_consumers())
    yield
    queue_depth_task.cancel()
    queue_consumer_task.cancel()
    logger.info("app_shutting_down")


app = FastAPI(
    title="AI CV Tailoring and Cover Letter Platform",
    description="Backend API for CV ingestion, extraction, matching, and generation.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — comma-separated, because the frontend is reachable under more than one
# origin at once: its stable per-environment URL and, in local development, the
# dev server. Deployment-specific URLs are deliberately not covered — their
# hostname carries an incrementing deployment number, so no fixed value matches.
# In local dev, also allow null origin (file:// pages) for the test harness.
_cors_origins = [o.strip() for o in settings.cors_origin.split(",") if o.strip()]
if settings.environment == "local":
    _cors_origins.append("null")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Correlation ID + request-timing middleware — inject a correlation_id into
# every request and record its latency. Combined into one middleware (rather
# than a second `@app.middleware("http")`) since both need to wrap the same
# call_next and Starlette runs middleware in registration order regardless.
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    import time
    import structlog
    from app.core.metrics import HTTP_REQUEST_DURATION_SECONDS

    corr_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    structlog.contextvars.bind_contextvars(correlation_id=corr_id)
    t_start = time.perf_counter()
    response = await call_next(request)
    duration_s = time.perf_counter() - t_start
    response.headers["X-Correlation-ID"] = corr_id

    # Route template ("/cvs/{cv_id}"), not raw path, to keep the route label
    # bounded — FastAPI sets scope["route"] once a route has matched.
    route = request.scope.get("route")
    route_path = route.path if route is not None else request.url.path
    HTTP_REQUEST_DURATION_SECONDS.labels(
        method=request.method, route=route_path, status_code=str(response.status_code),
    ).observe(duration_s)

    return response


# Security response headers. The API itself only ever serves JSON, so the
# strictest values are also the correct ones for every real endpoint: no
# page should be framed, sniffed into another content type, or allowed to
# load any sub-resource.
#
# The exception is the interactive documentation. /docs and /redoc are
# genuine HTML pages that pull Swagger UI and ReDoc assets from a CDN, so
# default-src 'none' renders them blank. Those two routes get a policy
# permitting exactly those sub-resources and nothing more.
_DOCS_PATHS = {"/docs", "/redoc", "/docs/oauth2-redirect"}

_DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'"
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path in _DOCS_PATHS:
        response.headers["Content-Security-Policy"] = _DOCS_CSP
    else:
        response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


# Standard error handler — returns the ErrorResponse envelope
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from datetime import datetime, timezone

    logger.error("unhandled_exception", path=str(request.url), error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "status": 500,
            "code": "INTERNAL_ERROR",
            "message": "An internal error occurred.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": str(request.url.path),
        },
    )


# Mount routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(cvs_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(job_posts_router, prefix="/api/v1")
app.include_router(matches_router, prefix="/api/v1")
app.include_router(resume_rewrites_router, prefix="/api/v1")
app.include_router(cover_letters_router, prefix="/api/v1")
app.include_router(trial_sessions_router, prefix="/api/v1")
app.include_router(tailored_cvs_router, prefix="/api/v1")
app.include_router(exports_router, prefix="/api/v1")
app.include_router(coverage_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/api/v1")
app.include_router(client_metrics_router, prefix="/api/v1")
app.include_router(applications_router, prefix="/api/v1")
app.include_router(job_feed_router, prefix="/api/v1")

# Prometheus metrics at /metrics
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


@app.get("/health")
async def health():
    """Health check endpoint. Returns 200 if the API is running."""
    return {"status": "ok"}