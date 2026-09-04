# Bug Report and Fix Status

Full account of every bug identified across this project's code review cycles, their current fix status (independently verified against the actual codebase, not taken on report), and recommended fixes for everything still open. Includes a newly reported production issue (orphaned processing job) added at the end with its own diagnosis and recommended fix.

**Verification basis:** all "fixed"/"not fixed" statuses below were confirmed directly against the code in the most recent backend upload, not inferred from commit messages, PR descriptions, or developer self-reports. Several items in this project's history were reported as done and turned out not to be — see `12-project-status-and-roadmap.md` for the precedent. This report follows the same discipline: every claim here was checked, not assumed.

---

## Part 1 — Fixed and confirmed working

### Bug #1 — Malformed bullet-list regex in job post parser
**File:** `app/extraction/job_post_parser.py`
**Was:** `_extract_bullet_list()`'s regex `r"^[-•*✦➤►\d+[.)]\s]"` had an invalid character-class range and matched nothing at all — every job post's `responsibilities` and `qualifications` extracted as an empty list, silently, with no error.
**Fix applied:** Replaced with `_BULLET_RE = re.compile(r"^\s*(?:[-•*✦➤►]|\d+[.)])\s+")`, consolidated to one module-level pattern.
**Verification:** Re-tested against `-`, `•`, `1.`, `2)` bullet formats and plain non-bulleted text — all match correctly now, non-bullets correctly rejected. ✅ **Confirmed fixed.**

### Bug #5 — Bare "Skills" heading not recognized
**File:** `app/extraction/heading_canonicalizer.py`
**Was:** Every SKILLS pattern required a qualifier word (`technical skills`, `key skills`, etc.) — a CV with a plain "Skills" heading, likely the single most common real-world variant, returned `unknown`.
**Fix applied:** Added `(re.compile(r"\bskills?\b", re.I), SKILLS)` as the last entry in the SKILLS pattern group, so more specific patterns still win via the existing coverage-ratio scoring.
**Verification:** Re-tested — `"Skills"` now correctly returns `skills`; `"Technical Skills"`, `"Areas of Expertise"`, `"Key Skills"` all still resolve correctly; unrelated headings (`"Career History"`, `"Career Objective"`) unaffected. ✅ **Confirmed fixed.**

### Bug #6 — Auth response schema serialization inconsistency
**File:** `app/schemas/auth.py`
**Was:** Auth response schemas returned snake_case (`access_token`) while every other schema file, and the OpenAPI spec (`05-openapi.yaml`), used camelCase (`accessToken`) — an undocumented, inconsistent API contract.
**Fix applied:** Added `Field(alias=...)` to every field, matching the pattern already used correctly in `app/schemas/cv.py`, with `populate_by_name` configured so requests still accept snake_case input.
**Verification:** Confirmed FastAPI's actual default is `response_model_by_alias=True` (checked directly against the installed library, not assumed), so no additional route-level config was needed. Traced the fix end-to-end via simulated serialization: output is correctly `accessToken`/`refreshToken`/`accountStatus`/`createdAt`. Test harness (`app.js` lines 87, 99) updated to match. Swept remaining schema files (`cv.py`, `jobs.py`) — no other instances of the same issue found. ✅ **Confirmed fixed, fully verified end-to-end.**

### Bug #4 — Stale `job_type` across multi-stage pipeline transitions
**File:** `app/workers/worker_jobs.py`
**Was:** Multi-stage pipelines (Docling→Textract→merge; job-post fetch→parse) reused the same `ProcessingJob.id` across stages but never updated `job_type`, so `GET /jobs/{jobId}` reported a stale, misleading stage indefinitely once a job progressed past its first stage.
**Fix applied:** `job.job_type` is now updated to the next stage immediately before each handoff, committed, then the next task is enqueued.
**Verification:** Confirmed all five transitions present (`docling_extract → textract_extract`, two `→ merge_parse` paths, `→ cv_parse`, `→ job_post_parse`), and confirmed correct commit-before-enqueue ordering at every site (no race condition where a downstream worker could read stale state). ⚠️ **Code fix confirmed working — but the assigned documentation step (a note in `03-data-model.md` recording "one row represents the whole pipeline, `job_type` reflects current stage" as an intentional design decision) was never added.** This is a real gap: without that note, a future developer unfamiliar with the reasoning could plausibly "fix" this back to the broken behavior, thinking multiple job rows were the intended design.

**Remaining action:** Add the design-decision note to `03-data-model.md` §4 (modelling rules) or wherever `processing_jobs` is documented. This is a five-minute task with no code risk — just needs doing.

---

## Part 2 — Not fixed, despite being assigned and appearing worked-on

### Bug #3 — SSRF DNS-rebinding gap (Critical — highest priority open item)
**File:** `app/services/ssrf_safe_fetch.py`

**What was assigned:** Pin the TCP connection to the IP address already validated by `resolve_and_validate_ip()`, rather than letting the underlying HTTP client re-resolve the hostname at connect time (which allows a DNS-rebinding attacker to pass validation against a public IP, then have the actual connection resolve to a private/internal address). Agreed approach: switch to `httpx` with a pinned-resolver transport, since `http.client` doesn't cleanly support connecting by IP while preserving correct SNI/`Host` behavior for HTTPS.

**What was actually done:** `httpx==0.28.1` was added to `requirements.txt`, but the file still imports and uses `http.client.HTTPConnection`/`HTTPSConnection` directly, and `httpx` is not imported or used anywhere in the codebase. More specifically: `resolve_and_validate_ip(host)` is still called and its result (`ip_addr`) is computed and validated — but **that validated IP is never passed to `_connect_and_read()`**, which still receives and connects using the raw hostname string. The variable is computed and then silently discarded. This has the appearance of an interrupted refactor — a dependency staged in preparation for a fix that was never completed — rather than a failed attempt at the fix itself.

**Current risk:** The vulnerability described in `10-security-plan.md` §4 is live and unmitigated. `POST /job-posts/url` fetches a user-supplied URL server-side; an attacker who controls DNS for a hostname they submit can have it resolve to a public IP during validation and a private/internal IP (including the cloud metadata endpoint, `169.254.169.254`) at actual connection time, bypassing the SSRF protection entirely. Given this endpoint's stated purpose is fetching arbitrary user-supplied URLs, this is not a theoretical edge case.

**Recommended fix, precisely:**
1. Change `_connect_and_read()`'s signature to accept the validated `ip_addr` as a parameter, alongside the original `host` (needed separately for the `Host` header and TLS SNI).
2. Use `httpx` with a custom transport that connects to the pinned IP directly. `httpx.HTTPTransport` (or `AsyncHTTPTransport`) supports a custom connection-pool override, but the cleanest approach is connecting to the pinned IP directly while setting `extensions={"sni_hostname": host}` on the request when connecting over HTTPS, which solves the SNI/cert-validation problem the original `http.client` approach couldn't cleanly handle.
3. Concretely:
   ```python
   import httpx

   def _connect_and_read(scheme, host, ip_addr, port, path, timeout, max_bytes):
       url = f"{scheme}://{ip_addr}:{port}{path}"
       headers = {"Host": host, "User-Agent": "CV-Tailoring/1.0"}
       extensions = {"sni_hostname": host} if scheme == "https" else {}
       with httpx.Client(timeout=timeout, follow_redirects=False) as client:
           with client.stream("GET", url, headers=headers, extensions=extensions) as response:
               if response.status_code in (301, 302, 303, 307, 308):
                   return b"", response.headers.get("location")
               if response.status_code >= 400:
                   raise FetchError(f"HTTP {response.status_code} fetching {scheme}://{host}{path}")
               total = 0
               chunks = []
               for chunk in response.iter_bytes(chunk_size=8192):
                   chunks.append(chunk)
                   total += len(chunk)
                   if total >= max_bytes:
                       raise FetchError(f"Response exceeds maximum size of {max_bytes} bytes.")
               return b"".join(chunks), None
   ```
4. Update the call site (currently around line 104) to pass `ip_addr` (already computed at line ~101, just currently unused) into the new signature.
5. Update the docstring — it currently claims "no dependencies beyond the standard library," which will no longer be true and shouldn't be left stale.
6. Re-run the full SSRF test suite from `10-security-plan.md` §4 and `09-test-plan.md` after the change — this alters the actual connection mechanism, so the legitimate-URL happy path (redirects, real HTTPS certs) needs re-confirming alongside the security fix, not just the attack cases.
7. Specifically add a DNS-rebinding regression test: mock `socket.getaddrinfo` (or use a test DNS name you control) to return a public IP on the validation call and a private IP on a hypothetical second resolution, and confirm the connection still goes to the IP validated at check-time — this is the one test that would have caught the current gap and doesn't yet exist.

### Bug #2 — Contradictory/Unclear support levels never implemented (High priority)
**File:** `app/extraction/match_engine.py`, `app/db/models.py`

**What was assigned:** Implement `unclear` first (thread source-item confidence into `_match_requirement()`, return `unclear` below a threshold), then `contradictory` as a separate CV-internal-consistency pre-pass (detecting overlapping-but-inconsistent date ranges in `cv_experience_items`). Defer only the fallback-substring-matching hardening to a follow-up ticket.

**What was actually done:** The `CONTRADICTORY` and `UNCLEAR` string constants are declared in `match_engine.py`, but **no code path anywhere assigns either value** — `_match_requirement()` still has exactly the same three branches (`supported`/`partially_supported`/`unsupported`) as before. The `MatchRun` model has no `contradictory_count`/`unclear_count` columns — no migration was written. This means the part of Batch D that was explicitly *not* deferred (the two new support levels) hasn't started; only the constant names exist, unused.

**Current risk:** Every test written against `09-test-plan.md` §5's contradictory/unclear evidence cases has no code path to exercise. The matching engine's own module docstring claims "contradictory evidence is flagged, not silently resolved" — that claim is currently false. This is a correctness gap, not a security one, but it's a gap in the system's core non-fabrication promise: a CV with genuinely conflicting internal claims (two different employment dates for what looks like the same role) currently gets no special handling at all.

**Recommended fix, in order:**
1. **Schema first.** Add `contradictory_count` and `unclear_count` (both `Integer, nullable=True`) to `MatchRun`. Confirm the `support_level` column/constraint on `MatchEvidenceItem` actually permits all five values (check for a DB-level `CHECK` constraint or application-level enum that might still reject the new values even after the Python constants exist). Write and test the Alembic migration, including that it's reversible.
2. **Implement `unclear` first**, since it's the more contained change:
   - `_match_requirement()` currently only receives skill names and a flattened text blob — it needs the *confidence* of the underlying `cv_experience_items`/`cv_skill_items` row(s) it's matching against, so thread that through the function signature.
   - Add a confidence threshold (start conservative — e.g. below 0.4 on whatever scale the corrected Docling confidence now uses, see the confidence-score fix already shipped) below which a match returns `unclear` instead of `supported`/`partially_supported`, regardless of textual match quality.
   - Test: construct a CV where the relevant section extracted with deliberately low confidence (reuse the "garbled document" test fixture from the confidence-score fix) and confirm the resulting match is `unclear`, not `supported`.
3. **Implement `contradictory` as a separate pre-pass**, run once per CV profile before requirement matching starts:
   - Compare `cv_experience_items` rows for the same profile version, looking for entries with matching or near-matching `company`/`title` but inconsistent, overlapping date ranges.
   - Produce a set of "these specific CV claims conflict" flags, keyed by the affected `cv_experience_items.id`s.
   - When `_match_requirement()` builds evidence and one of its candidate sources is flagged as internally contradictory, return `contradictory` instead of whatever level it would otherwise assign, with `source_references` pointing at both conflicting rows.
   - Test: construct a CV with genuinely conflicting employment dates for the same apparent role (reuse or extend the fixture already specified in `09-test-plan.md` §2/§5) and confirm the match result is `contradictory`, with both sources referenced.
4. **Update `process_match`** in `worker_jobs.py` to populate the two new count columns from the match run's results.
5. **Fix the module docstring** in `match_engine.py` to state accurately what's implemented once each piece lands — don't leave the current inaccurate claim in place even temporarily; a docstring asserting a guarantee the code doesn't provide is worse than no docstring.
6. **Defer, as agreed:** the fallback-substring-matching hardening (restricting `partially_supported` to occurrences near skill/experience-tagged content, rather than anywhere in the flattened CV text) — track this as its own follow-up ticket once `unclear`/`contradictory` are shipped and have test coverage to catch a regression, rather than bundling a fourth change into an already-large piece of work.

### CR-3 — Soft-deleted CVs remain fully readable (External assessment finding — Critical, confirmed real)
**File:** `app/api/v1/cvs.py`

**Finding:** All six CV read endpoints (`list_cvs`, `get_cv`, `reprocess_cv`, `get_cv_raw_text`, `get_cv_extraction_detail`, `get_cv_parsed_profile`) filter by `user_id` but never by `deleted_at.is_(None)`. `delete_cv()` sets `deleted_at` and `status = "failed"` but nothing prevents the row from being read normally afterward. Confirmed directly: `get_cv()` returns full metadata unconditionally for any row matching `id` + `user_id`, with no check against either the deletion timestamp or the resulting status.

**Current risk:** A user-initiated deletion doesn't actually remove access to the data — the CV, its extracted text, and its parsed profile remain fully retrievable via the API indefinitely. This directly contradicts `06-non-functional-requirements.md`'s deletion requirements ("user-facing deletion must actually remove documents and derived records... not just soft-delete flags") and is a genuine data-retention/privacy gap, not a cosmetic one.

**Recommended fix:**
1. Add a shared query helper in `cvs.py`:
   ```python
   def _active_cv_query(user_id: str):
       return select(CvFile).where(
           CvFile.user_id == user_id,
           CvFile.deleted_at.is_(None),
       )
   ```
2. Replace the base query in all six endpoints with this helper (or the equivalent `.where(CvFile.id == cv_id, ...)` variant with the same two conditions added).
3. Rename `delete_cv()`'s `status = "failed"` to `status = "deleted"` for clarity — reusing `"failed"` to also mean "deleted" conflates two different states and makes the status field ambiguous for anything reading it later (monitoring, audit review).
4. Add the direct regression test: delete a CV, then attempt each of the six read endpoints, confirm all return `404`.
5. While in this code: confirm the hard-delete path required by `06-non-functional-requirements.md` (actual removal of derived records after a retention period, not just the soft-delete flag) exists somewhere else in the codebase or is tracked as separate follow-up work — this fix addresses the *immediate* access-control gap, not the full retention-policy requirement.

### MP-4 — Raw SQL in `_get_next_attempt` (External assessment finding — not a real bug, filed as informational)
**File:** `app/workers/worker_jobs.py`

**Finding:** Confirmed the code correctly uses SQLAlchemy's `text()` with fully parameterized bind values (`:cv_id`, `:pt`, passed via a separate dict, never string-interpolated into the query). This is safe, idiomatic SQLAlchemy 2.x usage with no SQL injection risk. The external assessment's own text hedges "low risk here," but files the item as a Medium-severity security finding, which overstates it.

**Recommendation:** No security fix needed. If the team wants full-ORM consistency with the rest of the codebase (a legitimate style preference, not a risk mitigation), rewrite as `select(func.max(CvExtractionPass.attempt_number)).where(...)` at low priority, whenever that file is next touched for another reason — not worth a dedicated ticket on its own.

---

## Part 3 — Newly identified: orphaned processing job (production incident)

This wasn't found through code review — it's a live production/staging symptom, documented separately, and worth tracking alongside the code-review bugs above since it may share root causes with some of them.

### Issue — CV stuck in `pending` with a `queued`-but-never-consumed processing job

**Symptom:** CV `55ce54c9-a9bc-4f2b-8a5c-af22e5cb1fc7` (`test_cv.pdf`) has `status: pending`, zero `cv_extraction_passes` rows, zero `cv_raw_text` rows, and its `processing_jobs` row (`47297272-5504-4ab4-a774-170f003d7531`) is `status: queued` with no `started_at`. Three CVs uploaded around the same general period completed normally end-to-end, and the same Docling worker processed other jobs successfully in the same window — ruling out a fully-dead worker or a systemic parser/Textract/merge failure. Three job-post match jobs show the same orphaned pattern.

**Diagnosis, as investigated:** The task was published (`send_task()` returned without an exception) but the worker never received or executed it — an orphaned message between the API producer and the Celery/Redis broker, not a worker crash and not (for this specific CV) a missing consumer.

**What I can confirm directly from the code, resolving two of the investigation's open questions:**
- **The hypothesis that a `merge_parse` worker consumer might be missing is ruled out** — `docker-compose.yml` defines `worker_merge` listening on `-Q merge_parse` (confirmed present), alongside confirmed consumers for `docling_extract`, `textract_extract`, `job_post_fetch`, `job_post_parse`, and `cv_parse`. This specific CV's failure is not caused by a missing queue consumer.
- **The core hypothesis (no publish-retry reliability) is independently confirmed in the code.** `app/workers/tasks.py` sets `task_acks_late=True` (correct — a worker crash mid-task won't lose it), but none of the seven `send_task()` calls pass a `retry`/`retry_policy` argument. Celery's `send_task` does not retry a failed broker publish by default without this being explicitly configured. This means any transient connection blip between the API process and Redis at the moment of publication — a brief network hiccup, a connection-pool exhaustion moment, a broker reconnect window — silently drops the task with no error surfaced anywhere, exactly matching the observed symptom (no exception raised, task never reaches Redis or is never consumed).

**Recommended fix, in order of priority:**

1. **Add publish-retry configuration to every `send_task()` call** — this is the direct fix for the confirmed gap:
   ```python
   celery_app.send_task(
       "app.workers.worker_jobs.process_docling_extract",
       args=[job_id],
       queue="docling_extract",
       retry=True,
       retry_policy={
           "max_retries": 3,
           "interval_start": 0,
           "interval_step": 0.5,
           "interval_max": 3,
       },
   )
   ```
   Apply this to all seven `send_task` call sites in `tasks.py` uniformly — a shared wrapper function (`_send_task_with_retry(name, args, queue)`) is worth introducing here rather than repeating the retry policy seven times, both for consistency and so a future tuning change happens in one place.

2. **Add task-ID and publish-outcome logging around every `send_task()` call**, per the investigation doc's own recommendation:
   ```python
   logger.info("publishing_task", job_id=job_id, task_name=task_name, queue=queue_name)
   result = celery_app.send_task(...)
   logger.info("task_publish_confirmed", job_id=job_id, celery_task_id=result.id, queue=queue_name)
   ```
   This closes the current diagnostic gap directly — right now there's no way to distinguish "publish never happened," "publish happened but the message was lost in Redis," and "message was received but the worker never picked it up," and all three currently look identical from outside (a job stuck at `queued`). This logging is what makes that distinction possible for the *next* occurrence, not just this one.

3. **Add an outbox/recovery mechanism**, as the investigation doc proposes — this is the right long-term fix, not just a patch:
   - Add `published_at`, `celery_task_id`, `publish_attempts`, `last_publish_error` columns to `processing_jobs`.
   - A lightweight recovery task (scheduled, e.g. every few minutes) finds rows where `status = 'queued' AND published_at IS NULL AND created_at < now() - interval '2 minutes'` and republishes them using the existing enqueue functions.
   - The republish operation must be idempotent — verify this explicitly with a test that runs recovery twice against the same stuck job and confirms no duplicate `cv_extraction_passes` rows or corrupted state results, exactly as the investigation doc specifies.
   - This is a genuinely valuable general reliability improvement independent of this specific incident — it converts "an orphaned task requires manual DB investigation to notice" into "the system self-heals within minutes and logs that it did."

4. **Fix the status-visibility issue** (the investigation doc's finding #3) as a smaller, independent improvement: the CV list API currently maps `processing_status = cv_file.status` only, never surfacing `processing_jobs.status`. A user staring at a stuck upload currently sees a bare `"pending"` with no indication of whether a worker has even picked it up. Expose the combined state (e.g. `"Uploaded" / "Queued" / "Extracting" / ...` per the investigation doc's suggested model), or at minimum surface `processing_jobs.status` alongside `cv_files.status` in the CV detail response, so a stuck job is visibly distinguishable from a slow-but-progressing one without a database query.

5. **Add guarded status transitions**, as the investigation doc recommends, to prevent any future race where an out-of-order update could move a CV's status backwards (e.g. a delayed message setting a CV back to `queued` after a faster worker already advanced it to `extracting`). The transition table already sketched in the investigation doc is a reasonable starting point — implement it as a small validation function called wherever `cv_file.status` is assigned, rejecting or logging (not silently applying) any disallowed transition.

6. **Recover the specific orphaned CV and job** per the investigation doc's own safe recovery procedure — verify the job is still `queued` with no `started_at`, verify the source CV still exists, republish the original `docling_extract` task via the existing enqueue function, and do not manually mark anything as completed. This should happen after (or alongside) item 1 above, not as a substitute for it — recovering this one record without adding retry/logging/recovery infrastructure just means the next orphaned task requires the same manual investigation from scratch.

**One thing worth flagging distinctly:** this issue and Bug #3 (SSRF) both involve request-time network reliability under the same broad theme (a call that can silently fail with no visible error and no retry) — but they're unrelated in root cause and shouldn't be conflated or fixed together. Bug #3 is a security validation bypass; this is a message-delivery reliability gap. Worth noting only because both point at the same general pattern worth watching for elsewhere in the codebase: any external or cross-process call (broker publish, HTTP fetch, S3 operation) that doesn't have explicit retry/error-surfacing is a candidate for the same class of silent failure.

---

## Summary table

| # | Issue | Status | Priority for remaining work |
|---|---|---|---|
| 1 | Bullet-list regex (job post parser) | Fixed, verified | — |
| 5 | Bare "Skills" heading | Fixed, verified | — |
| 6 | Auth schema camelCase | Fixed, verified end-to-end | — |
| 4 | Stale `job_type` | Code fixed, doc note missing | Low — 5-minute doc fix |
| 3 | SSRF DNS-rebinding | Not fixed (dependency staged only) | **Highest — security-critical, previously flagged as top priority and still open** |
| 2 | Contradictory/Unclear support levels | Not implemented (constants only) | High — core non-fabrication guarantee gap |
| CR-3 | Soft-deleted CVs still readable | Not fixed | High — active privacy/retention gap |
| MP-4 | Raw SQL in `_get_next_attempt` | Not actually a bug | — (optional style cleanup only) |
| New | Orphaned processing job / no publish retry | Newly identified | High — active production symptom with a confirmed code-level cause |

**Recommended next action:** Bug #3 (SSRF) and the orphaned-job publish-retry fix (item 1 under Part 3) are the two highest-priority open items — both are security/reliability gaps with a confirmed root cause and a specific, scoped fix ready to implement, not open-ended investigation. CR-3 is comparably urgent from a privacy standpoint and is a small, contained fix. Bug #2 (match support levels) is the largest remaining piece of work and is best sequenced after the above, consistent with the earlier decision to keep it as its own careful, well-tested change rather than bundling it with anything else.
