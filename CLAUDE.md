# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An AI-assisted CV tailoring and cover-letter platform. Users upload a CV and a
job posting; the system extracts/analyzes both, produces a match score with
evidence-backed reasoning, and generates a tailored CV and cover letter —
under a strict **non-fabrication constraint** (generated content must be
traceable to evidence in the source CV, never invented). FastAPI backend +
Celery workers + Next.js frontend, run locally via Docker Compose.

**`Implementation pack/`** is the authoritative spec — read it before making
architectural judgment calls, not just this file. Start with
`00-README.md` for the reading order. In particular:
`02-architecture-overview.md` (why it's built this way, the non-fabrication
constraint), `03-data-model.md` + `05-openapi.yaml` (schema/API ground truth),
`10-security-plan.md` (required before touching upload/URL-fetch/generation
code), `12-project-status-and-roadmap.md` (actual vs. planned state —
canonical over any other status claim).

Top-level dirs that aren't part of the app: `Example/` (a separate reference
git repo some extraction-service code was ported from, byte-identical),
`test-harness/` (a plain HTML/JS page for manual API testing — this is why
`main.py` allows CORS origin `"null"` in local env), `CV matching fix/`,
`Test Cvs/`, `;C` — scratch/reference material, not source.

## Commands

### Backend (`backend/`)

Full stack (Postgres, Redis, MinIO, ClamAV, Gotenberg, extraction service, all
Celery workers, Prometheus/Grafana/Alertmanager):
```
docker compose up -d
```
The API itself mounts `./app` read-only into the container, so editing files
under `backend/app/` doesn't require a rebuild — only `docker compose restart api`
(or let uvicorn's reloader pick it up, if enabled).

Tests run from the **host venv** (`backend/.venv`), against Docker-provided
Postgres/Redis, not inside the container:
```
cd backend
.venv/Scripts/activate       # Windows
pytest                        # full suite
pytest tests/test_match_analysis.py            # one file
pytest tests/test_match_analysis.py::test_name # one test
pytest -k "job_feed"          # by keyword
```
`tests/conftest.py` auto-points `DATABASE_URL`/`DATABASE_URL_ASYNC` at the
isolated `cv_tailoring_test` database and `REDIS_URL` at Redis db 1, runs
`alembic upgrade head`, and truncates all app tables (except append-only
`audit_events`) once per session. **Never manually set `DATABASE_URL` /
`DATABASE_URL_ASYNC` for pytest** — conftest already does this, and an
explicit override bypasses the isolation and can wipe the shared dev
database (it has happened before). The `cv_tailoring_test` database only
gets created automatically on a fresh Postgres volume
(`postgres-init/`); against an existing volume, create it once manually
(`CREATE DATABASE cv_tailoring_test OWNER cvapp;`).

Migrations:
```
alembic revision -m "description"   # new migration
alembic upgrade head                # apply
```

Regenerating the skills taxonomy (`app/data/skills_index.json`, ESCO + O*NET —
see `backend/README.md` for licensing/attribution) is a manual, rarely-needed
step via `scripts/ingest_esco.py` then `scripts/ingest_onet.py`.

Known host-venv quirk (Windows): importing `app.main` directly from the host
venv can segfault via `python-magic`'s libmagic binding; stub
`sys.modules["magic"]` first if you need to import app code outside pytest
(conftest/pytest itself doesn't hit this).

### Frontend (`frontend/`)

```
npm run dev          # dev server on :3100 (NOT :3000 — that's Grafana in docker-compose)
npm run build
npm run lint
npm test             # vitest run, single pass
npm run test:watch   # vitest watch mode
npx vitest run src/lib/__tests__/some.test.ts   # single file
npm run test:e2e     # playwright, against :3100 (starts the dev server if not already running)
```
`npm test`/`test:watch` require `NODE_OPTIONS=--no-experimental-webstorage`
(already wired into the scripts via `cross-env`) — Node 25 breaks vitest's
`localStorage` mock otherwise; this is an environment issue, not a code
regression, if tests suddenly fail on that.

Playwright e2e specs run with `workers: 1` deliberately: the backend rate-
limits per client IP and every Playwright worker is `127.0.0.1`, so running
specs in parallel shares one budget (5 trial sessions/hour, 5 auth
requests/minute, 10 uploads/hour) and produces 429s that look like app bugs.

## Backend architecture

**Request flow**: `app/main.py` wires CORS, structured logging (structlog),
Prometheus metrics, a correlation-ID + request-timing middleware, and mounts
routers from `app/api/v1/*` under `/api/v1`. Each router pairs with a
same-named module in `app/schemas/` (Pydantic request/response models) and
delegates business logic to `app/services/*`. `/metrics` (Prometheus) and
`/health` are also mounted directly on the FastAPI app.

**Async job pipeline**: long-running work (extraction, matching, generation,
export, coverage reports) goes through the `processing_jobs` table rather
than executing inline. An endpoint creates a `processing_jobs` row and
enqueues a Celery task; the client polls job status. Each job type has its
**own Celery queue and its own worker container** in `docker-compose.yml`
(`worker_text_extract`, `worker_match`, `worker_cv_generate`,
`worker_cover_letter_generate`, `worker_ats_check`, `worker_cv_analyze`,
`worker_export`, `worker_coverage_report`, `worker_job_fetch`,
`worker_job_parse`, `worker_job_feed`, plus `worker_maintenance` and a
`beat` scheduler) — this is deliberate isolation, not incidental structure.
`_KNOWN_JOB_TYPES` in `main.py` and the queue names in `docker-compose.yml`
must stay in sync; the API process itself polls queue depth (from
`processing_jobs`) and Celery consumer counts (via `inspect.active_queues()`)
into gauges every 15s/30s so "queue has a backlog but no worker" is a
detectable, page-able state, not silent.

**Network isolation**: two Docker networks. `no_internet` (internal, no
egress) carries traffic between the API, Postgres, and services that must
never reach the public internet: `extraction`, `gotenberg`, `clamav`. The
`default` network is for services that need real egress: the API itself
(LLM calls), `worker_job_feed` (public job-board APIs). When adding a new
worker, its network membership is a security decision, not a default.

**Extraction pipeline (current, v2)**: a single step — `process_text_extract`
(queue `text_extract`) POSTs uploaded file bytes to the `extraction` service
(a small Node/TS service under `backend/extraction-service/`, hosting
`Example/`'s extraction route code verbatim) and writes `cv_raw_text`.
**A previous 4-step pipeline (Docling → Textract → merge → cv_parse) was
decommissioned** and lives, unimported, under `backend/decommissioned/` —
read `decommissioned/README.md` before assuming Docling/Textract code paths
are live; `textract_enabled` in `core/config.py` is a vestigial field kept
only so old `.env` files don't fail validation.

**LLM usage** (`app/services/llm_client.py`, prompts in `app/prompts/`): two
model tiers — `openai_model` (cheaper, for structured analysis/classification/
extraction) vs `openai_model_generation` (for prose: resume rewrite, cover
letters). Every LLM-backed feature has an independent boolean kill switch in
`core/config.py` (`resume_analysis_enabled`, `resume_rewrite_enabled`,
`cover_letter_llm_generation_enabled`, `job_post_llm_enrichment_enabled`);
features with no deterministic fallback return an honest 503 when disabled
rather than degrading silently. Generated content is checked against source
evidence via a token-overlap threshold per feature
(`tailored_cv_evidence_overlap_threshold`, `cover_letter_evidence_overlap_threshold`,
`job_post_llm_evidence_overlap_threshold`) — this is the runtime enforcement
of the non-fabrication constraint, not just a prompting convention.

**Auth**: JWT access tokens, but every request re-validates against a live,
revocable `user_sessions` row (`core/security.py`) — so logout/incident
revocation is immediate regardless of the JWT's own expiry. A parallel
**trial session** system (`trial_sessions` table, `api/v1/trial_sessions.py`)
lets unauthenticated users run the CV/ATS-check flow with tight, separate
rate limits, since it's the one unauthenticated way to mint an identity that
consumes upload/generation/URL-fetch budget.

**Rate limiting** (`core/rate_limit.py`) is tiered by endpoint class — general,
auth, upload, generation, URL-fetch, trial-session-creation — each with its
own request/window config in `core/config.py`, not a single global limit.

**DB access defense-in-depth**: Postgres RLS policies (migration 018) plus a
non-superuser `app_runtime` role (migration 017) that the API can opt into
via `database_url_runtime_async` — opt-in and currently unset by default, so
RLS enforcement only actually applies once that cutover is made deliberately.

**Observability**: structlog (JSON logs) + `core/tracing.py` (OpenTelemetry) +
Prometheus (`core/metrics.py`, scraped from the API process only) +
`core/metrics_push.py` (Pushgateway, for counters that only ever increment
inside Celery worker processes — SSRF rejections, generation schema-
validation failures — which the API-only Prometheus scrape would otherwise
never see). Grafana/Prometheus/Alertmanager run as part of the same
docker-compose stack.

**Job feed** (`services/job_feed/`, `workers/job_feed_jobs.py`): a separate,
independent pipeline — `worker_job_feed` periodically pulls listings from
five free/keyless external job-board APIs (RemoteOK, Remotive, Arbeitnow,
Reed, USAJobs; the latter two need a free registration key) into
`feed_job_postings`, skipping any source gracefully if its credential is
unset rather than failing the whole refresh.

## Frontend architecture

Next.js 16 App Router (`src/app/`). Two distinct user flows:
- `src/app/try/*` — unauthenticated trial flow (upload → results → tailor),
  backed by trial sessions rather than accounts.
- `src/app/dashboard/*` — authenticated flow (CVs, jobs, matches, tailored
  CVs, cover letters, applications, job feed, settings) behind
  `src/app/dashboard/layout.tsx`.

API access goes through per-domain client modules in `src/lib/*-api.ts`
(`api.ts`, `auth-api.ts`, `dashboard-api.ts`, `applications-api.ts`,
`job-feed-api.ts`, `trial-api.ts`) rather than ad-hoc fetches in components.
Server state uses TanStack Query; client/session state (auth, trial session)
uses Zustand (`src/store/`).

Testing: Vitest + Testing Library for components/hooks/lib (co-located
`__tests__/` dirs), MSW for mocking API calls in tests (`src/test/msw/`),
Playwright for e2e (`e2e/`, against the real dev server on :3100).
