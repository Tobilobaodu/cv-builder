"""POST /match-analyses, POST /resume-rewrites, POST /resume-rewrites/pdf.

Split (jbs-solution-sheet.md S1) from one synchronous call into two: a
small, fast /match-analyses (score, gaps, tips — the "useless until
complete" JSON half) and a streamed /resume-rewrites (markdown, readable
as it arrives — S2). Neither needs a Celery job — see
app/services/resume_rewrite.py's docstring for why. No jobPostId is taken
on either — the job post text is passed straight through, because the
model does its own requirement extraction. That deliberately bypasses
steps 7-9.

The frontend calls /match-analyses first, shows the score immediately,
then calls /resume-rewrites with that analysis's stats attached as
grounding context and streams the markdown in below it — see the target
journey in jbs-solution-sheet.md's Workstream 1 intro.
"""

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import (
    check_generation_rate_limit,
    check_llm_budget,
    get_client_key,
    record_llm_spend,
)
from app.core.security import (
    RequestIdentity,
    get_current_user_or_trial_session,
    get_scoped_session,
    identity_owner_filter,
    ownership_denied,
)
from app.db.models import CvFile, CvRawText
from app.services.resume_analysis import ResumeAnalysisError, analyze_resume
from app.services.resume_pdf import (
    MAX_MARKDOWN_CHARS,
    ResumePdfError,
    render_resume_pdf,
)
from app.services.resume_rewrite import ResumeRewriteError, stream_rewrite_resume

logger = get_logger(__name__)
router = APIRouter()


async def _load_raw_text(
    cv_id: str, identity: RequestIdentity, session: AsyncSession
) -> str:
    """Shared ownership + raw-text-ready check both endpoints below need.
    Deliberately duplicated rather than shared with cvs.py's own version
    of this check — this one 409s (caller should wait and retry) where a
    "not found" case is a 404, and the two endpoints here are the only
    callers."""
    cv_file = (
        await session.execute(
            select(CvFile).where(
                CvFile.id == cv_id,
                identity_owner_filter(CvFile, identity),
                CvFile.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if cv_file is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found",
        )

    raw_text = (
        await session.execute(
            select(CvRawText).where(CvRawText.cv_file_id == cv_file.id)
        )
    ).scalar_one_or_none()
    if raw_text is None or not (raw_text.canonical_text or "").strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="CV text is not ready yet. Wait for extraction to finish.",
        )
    return raw_text.canonical_text


# gpt-5-mini per-token pricing ($0.25/M prompt, $2.00/M completion —
# confirmed against OpenAI's pricing page), same source as
# COST_USD_COUNTER's other increment sites (worker_jobs.py) — duplicated
# here rather than imported since those live in worker code and these two
# calls run in the API process. Both /match-analyses (openai_model) and
# /resume-rewrites (openai_model_generation) currently resolve to the same
# model, hence one constant used at both call sites below — if the two
# settings are ever split to different models again, split this back into
# two rates at that point.
_GPT_5_MINI_RATE = (0.25 / 1_000_000, 2.00 / 1_000_000)  # prompt, completion


def _budget_identity(identity: RequestIdentity) -> tuple[str, str]:
    """(identity_key, tier) for check_llm_budget/record_llm_spend — keyed
    by whichever identity actually exists on this request, same precedence
    RequestIdentity itself uses."""
    if identity.user_id:
        return identity.user_id, "user"
    return identity.trial_session_id or "unknown", "trial"


def _require_budget(identity: RequestIdentity) -> tuple[str, str]:
    identity_key, tier = _budget_identity(identity)
    if not check_llm_budget(identity_key, tier):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily analysis limit reached for this account. Try again tomorrow.",
        )
    return identity_key, tier


# ──────────────────────────────────────────────────────────────────────
# POST /match-analyses
# ──────────────────────────────────────────────────────────────────────


class MatchAnalysisRequest(BaseModel):
    cv_id: str = Field(alias="cvId")
    job_description: str = Field(alias="jobDescription", min_length=40)
    target_title: str | None = Field(default=None, alias="targetTitle")

    model_config = {"populate_by_name": True}


class LiteralCoverage(BaseModel):
    """Deterministic keyword-in-text check (jbs-solution-sheet.md Q1) —
    the strict, synonym-blind bar a Taleo/Lever-class ATS keyword filter
    applies, reported alongside atsScore's semantic judgement rather than
    instead of it."""

    coverage: float
    present: list[str]
    absent: list[str]


class MatchAnalysisStats(BaseModel):
    cvOccupation: str = ""
    jobOccupation: str = ""
    sameOccupation: bool = True
    atsScore: float
    matchLabel: str
    matchedSkills: list[str]
    transferableSkills: list[str]
    missingSkills: list[str]
    priorityKeywords: list[str]
    literalCoverage: LiteralCoverage


class MatchAnalysisResponse(BaseModel):
    matchNotes: list[str]
    informationNeeded: list[str]
    stats: MatchAnalysisStats
    promptVersion: str


@router.post("/match-analyses", response_model=MatchAnalysisResponse)
async def create_match_analysis(
    request: Request,
    body: MatchAnalysisRequest,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Score a CV against a job post — small, fast, no tailored CV.

    Trial-accessible. Rate-limited per client IP (generation tier). Meant
    to return well before /resume-rewrites finishes streaming — pass its
    `stats` straight into that call's `analysis` field so the rewrite is
    grounded in the same assessment shown on screen.
    """
    if not settings.resume_analysis_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis is temporarily unavailable. Please try again shortly.",
        )
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many analysis requests. Please wait and try again.",
        )
    identity_key, _tier = _require_budget(identity)

    cv_text = await _load_raw_text(body.cv_id, identity, session)

    try:
        result = analyze_resume(
            cv_text=cv_text,
            job_post_text=body.job_description,
            target_title=body.target_title,
        )
    except ResumeAnalysisError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)
        ) from e

    prompt_rate, completion_rate = _GPT_5_MINI_RATE
    record_llm_spend(
        identity_key,
        result.prompt_tokens * prompt_rate + result.completion_tokens * completion_rate,
    )

    logger.info("match_analysis_served", cv_id=body.cv_id)
    return MatchAnalysisResponse(
        matchNotes=result.match_notes,
        informationNeeded=result.information_needed,
        stats=MatchAnalysisStats(**result.stats),
        promptVersion=result.prompt_version,
    )


# ──────────────────────────────────────────────────────────────────────
# POST /resume-rewrites — streamed
# ──────────────────────────────────────────────────────────────────────


class ResumeRewriteRequest(BaseModel):
    cv_id: str = Field(alias="cvId")
    job_description: str = Field(alias="jobDescription", min_length=40)
    target_title: str | None = Field(default=None, alias="targetTitle")
    candidate_notes: str | None = Field(default=None, alias="candidateNotes")
    # The stats object from this same job's /match-analyses call, sent
    # back as grounding context — see resume_rewrite_prompts.py's
    # build_user_payload. Optional: a caller that skipped analysis (or
    # whose analysis failed) still gets a rewrite, just a less-targeted
    # one, rather than being blocked entirely.
    analysis: dict | None = None

    model_config = {"populate_by_name": True}


def _sse_event(event_type: str, **fields) -> str:
    return f"data: {json.dumps({'type': event_type, **fields})}\n\n"


@router.post("/resume-rewrites")
async def create_resume_rewrite(
    request: Request,
    body: ResumeRewriteRequest,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Rewrite a CV for a job post, streamed as markdown (jbs-solution-
    sheet.md S2). Each SSE `data:` line is JSON: {"type": "delta", "text":
    "..."} to append, {"type": "corrected", "text": "...", ...} to
    *replace* everything rendered so far (rare — see
    resume_rewrite.py::stream_rewrite_resume), or {"type": "done", ...} /
    {"type": "error", "detail": "..."} to end the stream.

    Trial-accessible. Rate-limited per client IP (generation tier) — this
    is the most expensive call in the system now that it carries the
    whole CV and the whole job post in one prompt. Ownership/raw-text
    checks happen before the stream opens, so a 404/409 is a normal JSON
    error response, not something buried inside the SSE stream.
    """
    if not settings.resume_rewrite_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CV tailoring is temporarily unavailable. Please try again shortly.",
        )
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many rewrite requests. Please wait and try again.",
        )
    identity_key, _tier = _require_budget(identity)

    cv_text = await _load_raw_text(body.cv_id, identity, session)

    def event_stream():
        usage: dict = {}
        try:
            for event in stream_rewrite_resume(
                cv_text=cv_text,
                job_post_text=body.job_description,
                target_title=body.target_title,
                candidate_notes=body.candidate_notes,
                analysis=body.analysis,
                usage_sink=usage,
            ):
                if event.type == "delta":
                    yield _sse_event("delta", text=event.text)
                else:
                    yield _sse_event(
                        event.type,
                        text=event.text,
                        informationNeeded=event.information_needed,
                    )
        except ResumeRewriteError as e:
            logger.error("resume_rewrite_stream_error", cv_id=body.cv_id, error=str(e))
            yield _sse_event("error", detail=str(e))
        finally:
            # Best-effort: a client that disconnects before the model's
            # final usage-bearing chunk arrives leaves `usage` empty, which
            # under-counts this one call rather than blocking the stream
            # on spend accounting — same trade record_llm_spend's own
            # caller-facing contract already makes.
            if usage:
                prompt_rate, completion_rate = _GPT_5_MINI_RATE
                record_llm_spend(
                    identity_key,
                    usage.get("prompt_tokens", 0) * prompt_rate
                    + usage.get("completion_tokens", 0) * completion_rate,
                )

    logger.info("resume_rewrite_stream_started", cv_id=body.cv_id)
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # A reverse proxy that buffers this defeats the entire point
            # of streaming — see jbs-solution-sheet.md S2's verify step.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class ResumePdfRequest(BaseModel):
    """The Markdown to render.

    Taken from the request rather than looked up, because the rewrite is
    stateless — there is no draft row to reference. The content is the
    caller's own CV either way, and it is HTML-escaped before rendering.
    """

    markdown: str = Field(
        alias="tailoredResumeMarkdown", min_length=1, max_length=MAX_MARKDOWN_CHARS
    )
    file_name: str | None = Field(default=None, alias="fileName")

    model_config = {"populate_by_name": True}


def _safe_filename(raw: str | None) -> str:
    """Keep the download name to characters that survive a Content-Disposition
    header unquoted — never let a caller inject header syntax."""
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]", "", (raw or "").strip())[:80].strip()
    if not cleaned:
        cleaned = "tailored-cv"
    if not cleaned.lower().endswith(".pdf"):
        cleaned = f"{cleaned}.pdf"
    return cleaned


@router.post("/resume-rewrites/pdf")
async def create_resume_rewrite_pdf(
    request: Request,
    body: ResumePdfRequest,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
):
    """Render tailored-CV Markdown to a PDF and return it inline.

    Synchronous like the rewrite itself: Gotenberg converts in well under
    a second, so a job row and a poll would cost more than they save.
    Requires an identity so it cannot be used as an open render service,
    and shares the generation rate-limit tier.
    """
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many export requests. Please wait and try again.",
        )

    try:
        pdf = render_resume_pdf(body.markdown)
    except ResumePdfError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)
        ) from e

    filename = _safe_filename(body.file_name)
    logger.info(
        "resume_pdf_rendered",
        user_id=identity.user_id,
        markdown_chars=len(body.markdown),
        pdf_bytes=len(pdf),
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
