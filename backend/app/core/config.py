"""Application configuration via Pydantic Settings.

Loads from environment / .env files. Secret-shaped values (DB creds,
AWS keys, JWT secrets) must come from env — never hardcoded defaults.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    app_name: str = "cv-tailoring-backend"
    port: int = 8000
    log_level: str = "info"
    environment: str = "local"

    # Database
    database_url: str = ""
    database_url_async: str = ""
    # Runtime connection for the API process only — a non-superuser,
    # non-owner role so Postgres RLS policies (migration 018) actually
    # apply to the app's own queries instead of being silently bypassed.
    # Falls back to database_url_async when unset (see app/db/session.py),
    # so this is opt-in: nothing changes until the app_runtime role has
    # been provisioned (migration 017) and this is deliberately set.
    database_url_runtime_async: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # AWS
    aws_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    s3_bucket_name: str = "cv-tailoring-local"

    # MinIO (S3-compatible local)
    minio_endpoint: str = "http://localhost:9000"
    minio_root_user: str = "minioadmin"
    minio_root_password: str = "minioadmin"

    # Textract — DECOMMISSIONED (step 4). Kept so existing .env files with
    # TEXTRACT_ENABLED set don't fail validation; nothing reads it now.
    textract_enabled: bool = False

    # Extraction service — hosts Example's routes/extract.ts unchanged and
    # replaces decommissioned steps 3-6. Reachable on the no_internet
    # network only; it needs no egress.
    # 5050 is what docker-compose.yml asks the extraction service to listen on,
    # and is correct for a local `docker compose up`. It is NOT correct on a
    # platform that injects its own PORT into every compose service — Temps
    # does, so the service there listens on the injected port instead and this
    # default dials a closed one, failing extraction with "Connection refused".
    # Deployed environments must therefore set EXTRACTION_SERVICE_URL to match
    # whatever port the platform actually assigned.
    extraction_service_url: str = "http://extraction:5050"
    extraction_service_timeout_seconds: int = 120

    # LLM (Phase 3)
    openai_api_key: str = ""
    # Default for structured/judgement tasks (analysis, classification,
    # extraction). Prose-quality generation (resume rewrite, cover letters)
    # opts into openai_model_generation instead — see llm_client.py callers.
    # These two used to disagree between here and .env.example; keep them
    # in sync if you change either.
    # Both point at gpt-5-mini for now (matches the Example reference app's
    # model choice) — kept as two separate settings rather than collapsed
    # into one so the tiers can diverge again later without a config
    # shape change. gpt-5-mini is a Chat Completions model that requires
    # max_completion_tokens instead of max_tokens — see llm_client.py.
    openai_model: str = "gpt-5-mini"
    openai_model_generation: str = "gpt-5-mini"
    openai_request_timeout_seconds: int = 30
    # Per-task timeouts: tight on the critical (synchronous, user-waiting)
    # path, looser on generation now that it runs off-path (streamed or
    # backgrounded). generate_structured()'s default max_api_retries=2
    # means analysis's worst case is timeout * 2, not * 3 - one retry only
    # (see analysis call sites, which pass max_api_retries=1).
    openai_timeout_analysis_seconds: int = 15
    openai_timeout_generation_seconds: int = 45

    # Kill switches (jbs-solution-sheet.md C2) — mirrors
    # cover_letter_llm_generation_enabled below, but these two have no
    # template fallback to drop to (there's no deterministic "resume
    # analysis" or "resume rewrite" absent the model), so disabling one
    # means the endpoint returns an honest 503 rather than degrading.
    # During a provider outage this is the difference between a
    # maintenance banner and a support queue.
    resume_analysis_enabled: bool = True
    resume_rewrite_enabled: bool = True

    # Tailored CV generation (Sprint 3)
    tailored_cv_evidence_overlap_threshold: float = 0.35
    tailored_cv_max_generation_retries: int = 2
    tailored_cv_max_experience_items: int = 6
    tailored_cv_max_project_items: int = 4
    tailored_cv_max_skill_items: int = 24

    # Cover letter generation (Sprint 4)
    # Independently tunable from tailored_cv's threshold above — letters
    # carry more connective/boilerplate prose ("Dear Hiring Manager,",
    # "Thank you for your consideration") than CV bullets, which could
    # dilute token-overlap ratios differently. Provisional starting
    # value, not yet measured against real generated letters.
    cover_letter_llm_generation_enabled: bool = True
    cover_letter_evidence_overlap_threshold: float = 0.30
    cover_letter_max_generation_retries: int = 2
    cover_letter_fallback_max_stories: int = 2
    cover_letter_min_word_count: int = 100
    cover_letter_max_word_count: int = 350

    # Job post LLM skill-extraction enrichment (M3)
    # Only called when the rules-based+taxonomy parse (M1/M2) finds fewer
    # than this many combined required_skills+qualifications — targets
    # exactly the prose-heavy-posting gap M1/M2 can't close, rather than
    # spending an LLM call on every job post regardless of need.
    job_post_llm_enrichment_enabled: bool = True
    job_post_llm_enrichment_min_requirements: int = 3
    # Average word count above which extracted required_skills/
    # qualifications are judged to be prose sentences rather than skill
    # terms (verified against a real posting: 8 "qualifications" at
    # 11-24 words each — a healthy *count* that still needed enrichment,
    # since count alone doesn't catch this failure mode).
    job_post_llm_enrichment_prose_word_threshold: int = 9
    job_post_llm_evidence_overlap_threshold: float = 0.4

    # JWT
    jwt_secret: str = ""
    # Access-token lifetime. Raised from the original 1 hour — every request
    # already re-checks the bearer token against a live, revocable
    # user_sessions row (get_current_user, app/core/security.py), so logout
    # or an incident-response revoke still invalidates it immediately
    # regardless of this value; a longer value mainly means less-frequent
    # forced re-logins for a token that's never actually been revoked. No
    # refresh-token flow exists yet (the 30-day refresh token issued at
    # login is stored but nothing redeems it) — until that's built, this is
    # the only lever for session length.
    jwt_expiry: int = 604800  # 7 days

    # Rate limiting (tiered)
    rate_limit_requests: int = 100
    rate_limit_window: int = 60
    rate_limit_auth_requests: int = 5
    rate_limit_auth_window: int = 60
    rate_limit_upload_requests: int = 10
    rate_limit_upload_window: int = 3600
    rate_limit_generation_requests: int = 20
    rate_limit_generation_window: int = 3600
    rate_limit_url_fetch_requests: int = 20
    rate_limit_url_fetch_window: int = 3600
    max_concurrent_jobs_per_user: int = 5

    # Trial session creation (Sprint 2) — deliberately tighter than the
    # other tiers: this is the one unauthenticated way to mint a new
    # identity that can then consume upload/generation/url_fetch budget.
    rate_limit_trial_session_requests: int = 5
    rate_limit_trial_session_window: int = 3600
    trial_session_ttl_hours: int = 48
    trial_session_cleanup_interval_seconds: int = 3600

    # Stalled-job recovery (outbox/recovery): republish processing jobs
    # stuck at pending/queued with no started_at — orphaned between the API
    # producer and the Celery/Redis broker, or never consumed by a worker.
    stalled_job_recovery_enabled: bool = True
    stalled_job_min_age_seconds: int = 120
    stalled_job_recovery_interval_seconds: int = 120
    stalled_job_max_publish_attempts: int = 3

    # ClamAV
    clamd_host: str = "localhost"
    clamd_port: int = 3310

    # Exports (Sprint 5) — Gotenberg does DOCX→PDF conversion, called
    # over the internal Docker network only (no internet egress needed,
    # same isolation posture as the CV-parsing workers).
    gotenberg_url: str = "http://gotenberg:3000"
    gotenberg_request_timeout_seconds: int = 30

    # Free-API job feed (item 7) — 5 keyless-or-free-tier sources refreshed
    # periodically by app.workers.job_feed_jobs.refresh_job_feed (queue
    # "job_feed", worker_job_feed in docker-compose.yml — needs public
    # internet egress, deliberately kept off the no_internet-only workers).
    # RemoteOK/Remotive/Arbeitnow need no key at all. Reed and USAJobs each
    # need a free registration; refresh_all_sources skips a source
    # gracefully (logs and returns no rows) rather than failing the whole
    # refresh when its credential is unset — matches the kill-switch
    # posture used elsewhere in this file (e.g. resume_analysis_enabled).
    job_feed_refresh_interval_seconds: int = 10800  # 3 hours
    reed_api_key: str = ""  # https://www.reed.co.uk/developers
    usajobs_api_key: str = ""  # https://developer.usajobs.gov
    usajobs_user_agent_email: str = ""  # USAJobs requires the registered email as the request's User-Agent

    # Pushgateway (Sprint 6 live-fire finding) — counters that only ever
    # increment inside Celery worker processes (SSRF rejections, generation
    # schema-validation failures, real API spend) are invisible to Prometheus
    # otherwise, since it only scrapes the api service. See
    # app/core/metrics_push.py.
    pushgateway_url: str = "http://pushgateway:9091"

    # Observability. The platform supplies the destinations — a Temps
    # deployment arrives with OTEL_EXPORTER_OTLP_* and SENTRY_DSN already in
    # its environment — so these switches only decide whether this process
    # uses them, matching the kill-switch posture of the LLM features above.
    # Both are inert wherever those variables are absent (local runs, tests),
    # so leaving them on does not force a network dependency.
    otel_traces_enabled: bool = True
    error_tracking_enabled: bool = True

    # CORS — accepts a comma-separated list; see app/main.py.
    cors_origin: str = "http://localhost:3000"

    model_config = {"env_file": ".env.local", "env_file_encoding": "utf-8"}


settings = Settings()