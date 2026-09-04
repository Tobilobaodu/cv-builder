"""Prometheus metrics for the extraction pipeline.

- Job throughput (counter per job_type and status)
- Processing duration (histogram per job_type)
- Failure rate (derivable from counters)

In production, expose these via a sidecar metrics HTTP server on each
worker or a shared Pushgateway. For local dev, the API /metrics endpoint
serves API-side metrics plus these custom metrics in the same process.

Sprint 6 live-fire verification found this docstring's own caveat was real:
Prometheus only scrapes the API process (prometheus.yml has one target,
api:8000) — counters incremented inside Celery worker processes were
provably never reaching Prometheus (confirmed via a genuine SSRF rejection
that never showed up as a nonzero rate()). SSRF_REJECTED_COUNTER,
GENERATION_SCHEMA_VALIDATION_FAILED_COUNTER, COST_USD_COUNTER, and
EVIDENCE_VERIFICATION_COUNTER only increment in worker code, so those four
are also pushed to a Pushgateway (app/core/metrics_push.py) right after
the local .inc() — see PUSH_REGISTRY below: a counter's own .inc() call
being present is not sufficient, it must also be registered there, or the
push silently omits it (found live while adding
EVIDENCE_VERIFICATION_COUNTER — same failure mode this whole mechanism
exists to fix, one register() call away from repeating it).
QUEUE_DEPTH_GAUGE avoids the problem entirely by living in and being
updated from the API process itself (app/main.py's lifespan), since queue
depth is a property of the database, not of any one worker.
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

JOB_THROUGHPUT = Counter(
    "processing_jobs_total",
    "Total processing jobs completed, by type and status",
    ["job_type", "status"],
)

JOB_DURATION_SECONDS = Histogram(
    "processing_job_duration_seconds",
    "Processing job duration in seconds, by job_type",
    ["job_type"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0),
)

EXTRACTION_CHARS = Histogram(
    "extraction_characters",
    "Number of characters extracted, by pass_type",
    ["pass_type"],
    buckets=(100, 500, 1000, 2000, 5000, 10000, 20000, 50000),
)

MERGE_STRATEGY_COUNTER = Counter(
    "merge_strategy_used_total",
    "Which merge strategy was selected",
    ["strategy"],
)

STRUCTURAL_ANOMALY_COUNTER = Counter(
    "structural_anomalies_total",
    "Structural anomalies detected during merge validation",
    ["anomaly_detected"],
)

LLM_TOKENS_COUNTER = Counter(
    "llm_tokens_total",
    "LLM tokens used, by generation task and token type",
    ["generation_task", "token_type"],
)

LLM_GENERATION_COUNTER = Counter(
    "llm_generations_total",
    "LLM generation calls, by generation task and outcome",
    ["generation_task", "outcome"],
)

# ── Security / attack-pattern counters (Sprint 6, Workstream H) ─────────────
# These back the 5 alert patterns in prometheus/alert_rules.yml — §10 names
# them explicitly. They are intentionally counters (monotonic) so PromQL
# rate()/increase() can window them, not gauges.

AUTH_FAILURE_COUNTER = Counter(
    "auth_failures_total",
    "Authentication failures, by reason",
    ["reason"],  # wrong_password | unknown_email | expired_token | revoked_token
)

AUTHZ_DENIED_COUNTER = Counter(
    "authz_denied_total",
    "Cross-user resource access denials (IDOR probing / ownership 404s)",
)

SSRF_REJECTED_COUNTER = Counter(
    "ssrf_rejected_total",
    "SSRF-safe-fetch validation rejections",
)

GENERATION_SCHEMA_VALIDATION_FAILED_COUNTER = Counter(
    "generation_schema_validation_failed_total",
    "Generation schema-validation failures (possible prompt-injection attempts)",
)

# jbs-solution-sheet.md O1: the anti-fabrication gate in generation_core.py
# (generate_and_verify_section's evidence_binder.verify_claim_against_
# evidence call) previously incremented nothing of its own — only schema
# failures were visible, which is a different event with a different
# cause. This is the metric FabricationRateSpike (alert_rules.yml) alerts
# on: a rising "omitted" rate means the model is fabricating, or a prompt
# change broke grounding — no infrastructure metric shows either.
EVIDENCE_VERIFICATION_COUNTER = Counter(
    "evidence_verification_total",
    "Evidence verification outcomes for generated sections.",
    ["section_type", "outcome"],  # outcome: passed | rejected_retry | omitted
)

# jbs-solution-sheet.md O4: nothing else measures the thing actually cared
# about — HTTP_REQUEST_DURATION_SECONDS is per-route, but the 30-second-
# target journey spans several routes plus client-side poll lag (S5's 14s
# of dead time lived entirely there) and render time, neither visible to
# the server on its own. Recorded client-side and posted to
# POST /client-metrics/journey (app/api/v1/client_metrics.py) — this
# Histogram lives in the api process and is scraped normally, no push
# needed, since the beacon endpoint runs in-process, not in a worker.
JOURNEY_DURATION_SECONDS = Histogram(
    "journey_duration_seconds",
    "Wall clock from CV upload accepted to analysis rendered, measured client-side.",
    ["journey"],
    buckets=(3, 5, 7.5, 10, 15, 20, 30, 45, 60),
)

# jbs-solution-sheet.md O2: published work on LLM resume graders shows a
# bias toward scoring longer CVs higher, never validated against hiring
# outcomes. If median atsScore climbs monotonically across length_bucket,
# that bias is present here and the analysis prompt needs an explicit
# instruction that length is not evidence of fit — a question this metric
# answers and nothing else currently can. `le` buckets on the score
# itself (not a separate length histogram) so the length/score
# relationship reads directly off one panel: sum by (length_bucket) of
# each score bucket's count.
ANALYSIS_SCORE_BY_LENGTH = Histogram(
    "analysis_score_by_cv_length",
    "ats_score bucketed by CV character count.",
    ["length_bucket"],  # <2k | 2-4k | 4-8k | 8k+
    buckets=(10, 25, 40, 55, 70, 85, 100),
)


def length_bucket(char_count: int) -> str:
    """Shared by every ANALYSIS_SCORE_BY_LENGTH caller (resume_analysis.py,
    cv_analysis.py) so the bucket boundaries can't drift between them —
    two independently-tuned bucketings would make the O2 panel compare
    apples to oranges across the two scoring paths."""
    if char_count < 2000:
        return "<2k"
    if char_count < 4000:
        return "2-4k"
    if char_count < 8000:
        return "4-8k"
    return "8k+"

# ── Queue depth (fixes QueueDepthSpike, which referenced a label value —
# status="queued" — that processing_jobs_total never actually emits; the
# counter is only ever incremented with status="completed"/"failed", and
# only from within worker processes). This gauge is updated periodically
# from the database by app/main.py's lifespan, in the API process, so it
# needs no Pushgateway.
QUEUE_DEPTH_GAUGE = Gauge(
    "processing_queue_depth",
    "Current count of not-yet-completed processing jobs, by job_type",
    ["job_type"],
)

# ── Queue consumers. Depth alone cannot distinguish "work is queued and
# being worked through" from "work is queued and nobody is listening" —
# both look like a nonzero depth, and the second one never resolves. A
# job whose queue has no consumer sits at pending forever with no error,
# no timeout and no failed status: the API accepted it, the broker holds
# it, and the UI spins indefinitely. Confirmed live twice in one session
# (worker_textract, then worker_cv_generate), each time diagnosed only by
# reading `docker ps` by hand.
#
# recover_stalled_jobs does not cover this: it republishes to the same
# queue, so with no consumer it re-queues into the same void — correct
# for a lost publish, useless for a missing worker.
#
# Updated from the API process (like QUEUE_DEPTH_GAUGE) via Celery's
# control-plane inspect, so it needs no Pushgateway. -1 means "could not
# determine" (broker unreachable / inspect timed out) — deliberately not
# 0, so a failed poll can't masquerade as a missing worker and page
# someone at 3am for a network blip.
QUEUE_CONSUMERS_GAUGE = Gauge(
    "processing_queue_consumers",
    "Number of Celery workers currently consuming each queue (-1 = unknown)",
    ["job_type"],
)

# ── HTTP request latency (API process only — matches every other route
# handler's request/response cycle, not worker task duration, which
# JOB_DURATION_SECONDS already covers). Route template (e.g. "/cvs/{cv_id}"),
# not the raw path, to keep cardinality bounded across id-scoped routes.
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds, by method, route, and status code",
    ["method", "route", "status_code"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ── Real spend on paid external APIs (Textract, OpenAI), by call type.
# Token-based for LLM calls (real prompt_tokens/completion_tokens against
# documented gpt-4o-mini per-token pricing), per-page for Textract (real
# AWS DetectDocumentText per-page pricing) — see the increment sites in
# worker_jobs.py for the actual rates used.
COST_USD_COUNTER = Counter(
    "cost_usd_total",
    "Estimated real USD spend on paid external APIs, by call_type",
    ["call_type"],  # textract | cv_generate | cover_letter_generate
)

# ── Pushgateway registry: only the worker-side counters that Prometheus
# can't otherwise see. Deliberately NOT the whole default REGISTRY —
# JOB_THROUGHPUT etc. stay local-only-and-unscraped for now (a known,
# separate, lower-priority gap; not one of the 5 §10 alert patterns) so a
# partial per-worker snapshot of it doesn't leak into Pushgateway and look
# like a complete picture on a future dashboard.
PUSH_REGISTRY = CollectorRegistry()
PUSH_REGISTRY.register(SSRF_REJECTED_COUNTER)
PUSH_REGISTRY.register(GENERATION_SCHEMA_VALIDATION_FAILED_COUNTER)
PUSH_REGISTRY.register(COST_USD_COUNTER)
# O1: generate_and_verify_section runs in worker_cv_generate/
# worker_cover_letter_generate, so this needs the same Pushgateway path —
# registering it here is what actually makes push_worker_metrics's calls
# at each _record_verification site (generation_core.py) reach Prometheus,
# not just increment a value local to that worker process.
PUSH_REGISTRY.register(EVIDENCE_VERIFICATION_COUNTER)
# O2: cv_analysis.py's analyze_cv runs in worker_cv_analyze, not the api
# process — same reason as the three above.
PUSH_REGISTRY.register(ANALYSIS_SCORE_BY_LENGTH)