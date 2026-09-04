# Decommissioned — trial flow v1

Nothing here is deleted and nothing here is imported, typechecked or run.
`tsconfig.json` and `vitest.config.ts` both exclude this directory, and
Playwright only collects from `e2e/`.

## Why

`/try/upload` posted a CV plus a job post, then pushed the user to
`/try/results`, which polled `GET /cvs/{id}/parsed-profile` and then created
a match, a draft and an export. Every one of those depends on the structured
profile that step 6 (`cv_parse`) produced — and steps 3-6 are decommissioned
(see `backend/decommissioned/README.md`). `parsed-profile` now returns 404,
so the page could not get past its first poll.

`/try/upload` is now the single-call rewrite flow: upload on file selection,
extracted text in a slide-over, one `POST /api/v1/resume-rewrites` for the
tailored CV and its stats. `/try/tailor`, where that page was first built,
redirects to `/try/upload`.

The job post can still be pasted **or** fetched from a URL, as it could on
the old page. The URL path reuses `POST /job-posts/url` and the SSRF-guarded
`worker_job_fetch` unchanged; the page polls `GET /job-posts/{id}` for
`rawText` (written at status `structuring`, so it does not wait for
`job_post_parse`) and drops it into the textarea. Nothing downstream of the
fetch is used — the structured job profile from step 9 is not read.

There is no separate fetch button: on the URL tab, "Tailor my CV" fetches
and then analyses in one click. The fetched text is still written into the
textarea and left editable, so what gets analysed stays visible.

`worker_job_fetch` now runs the fetched body through
`app/services/job_post_extract.py` before storing it, which reduces a
careers page to the posting in three tiers: schema.org `JobPosting` JSON-LD
first (every major ATS emits it, because Google Jobs requires it), then the
`main`/`article` subtree, then the whole page with furniture dropped. On a
real Teamtailor posting that removed the cookie dialog, career menu,
employee login, colleague profiles, "About us" and the ATS footer. `ssrf_safe_fetch` returns
the response verbatim, so before that a URL-sourced posting was stored,
displayed and sent to the model as raw markup — including the whole of any
inline `<script>`. Pasted text is unaffected: the stripper returns its input
unchanged unless the body actually looks like HTML.

| File | Was |
|---|---|
| `trial_flow_v1/upload_page.tsx` | `src/app/try/upload/page.tsx` |
| `trial_flow_v1/upload_page.test.tsx` | `src/app/try/upload/__tests__/page.test.tsx` — asserts the react-hook-form validation and the `router.push("/try/results")` handoff |
| `trial_flow_v1/trial-flow.spec.ts` | `e2e/trial-flow.spec.ts` — drives upload → parse → match → generate → download end to end |
| `trial_flow_v1/auth-handoff.spec.ts` | `e2e/auth-handoff.spec.ts` — anonymous trial → cover-letter paywall → register → continuity back into `/try/results` |

`src/app/try/results/page.tsx` is left in place: it is unreachable from the
new flow but still compiles, and restoring step 6 would make it work again
without further edits.

`e2e/dashboard.spec.ts` was **edited, not moved** — it only ever needed the
upload itself to land, so it now selects a file and waits for extraction
instead of pressing "Run my match" and waiting for `/try/results`.

## The paywall is off, and every piece of it is still here

Decision (2026-08-19): leave the paywall disabled while the single-call flow
is evaluated, keep the code, reintroduce it for launch.

It is disabled by being **unreachable**, not by being removed or by a
feature flag. `PaywallDialog` is rendered in exactly one place,
`src/app/try/results/page.tsx`, and that page redirects to `/try` unless the
trial store already holds a `cvId` and a `jobPostId`. The new `/try/upload`
never writes either — it keeps its state locally and calls
`POST /api/v1/resume-rewrites` directly — so the route always bounces.
`e2e/tailor-flow.spec.ts` asserts that, so it cannot drift back on by
accident with a dead pipeline behind it.

Nothing was deleted. Still present and still tested:

| Piece | Where | Status |
|---|---|---|
| The dialog | `src/components/paywall-dialog.tsx` | unchanged, 4 unit tests still run |
| The CTA that opens it | `src/app/try/results/page.tsx` | unchanged, page unreachable |
| The account boundary | `app/api/v1/cover_letters.py` — all 7 endpoints stay `get_current_user`-only | unchanged and still enforced |
| The handoff e2e | `decommissioned/trial_flow_v1/auth-handoff.spec.ts` | not collected |

Note the backend boundary never moved: cover letters are still account-only.
Disabling the paywall did not open a paid feature to anonymous users — it
removed the *prompt*, because the flow that showed it is gone.

### Reintroducing it for live

1. Decide what the paid unit is. The old paywall gated the cover letter,
   which the new flow does not offer at all. If the tailored CV itself is
   the paid unit, the trigger is new work, not a restoration.
2. Persist something worth claiming. `POST /resume-rewrites` writes no row,
   so today there is nothing for a new account to inherit — see below.
3. Restore the handoff e2e from `trial_flow_v1/auth-handoff.spec.ts`, which
   is the only end-to-end proof the claim path works.
4. Delete the paywall-unreachable assertion in `e2e/tailor-flow.spec.ts`,
   which is deliberately written to fail once the paywall is wired back up.

## What this costs, and it is not small

`auth-handoff.spec.ts` is the one to read before deciding this is finished.
It covered the paywall, the trial-to-account handoff and dashboard
continuity — and none of those have anything to stand on any more. The new
flow is synchronous and stateless: it writes no match, no draft and no
export, so there is nothing for a new account to claim and nothing for
`/dashboard/continue` to show. The CV row and its extracted text are still
persisted; everything downstream of them is not.

That is a direct consequence of the accepted design (one call, nothing
stored), not a bug in it. But it means the monetisation path is currently
untested and unbacked, so it needs a decision before this ships.

## Restoring

1. Move the four files back to the paths in the table above (the new
   `/try/upload` page would need somewhere else to live — `/try/tailor` is
   free once its redirect is removed).
2. Drop `"decommissioned"` from `exclude` in `tsconfig.json` and
   `vitest.config.ts`.
3. Restore step 6 on the backend first, or the flow still dead-ends at
   `parsed-profile`.
