"""Load / soak test for the CV-tailoring pipeline (Sprint 6, Workstream J).

Exercises concurrent upload -> job-post -> match -> generation against the real
local Docker stack — the slowest, most expensive part of the pipeline, which
per the roadmap has never been load-tested since generation entered in Sprint 3.

Tool choice: plain asyncio + httpx (no new dependency) — a one-off soak doesn't
need Locust's UI/reporting, and httpx is already a runtime + test dependency.

What it watches for:
  - dropped jobs: a submitted job that never reaches a terminal status
    (completed/failed) within its window is a silent drop, not a graceful 429.
  - the queue-depth alert: after the run, read the API /metrics endpoint and
    report processing_queue_depth by job_type so QueueDepthSpike can be
    checked against real load (the script can't assert Alertmanager state
    itself). Post-sign-off correction: this used to read
    processing_jobs_total{status="queued"}, but that label value is never
    actually emitted (JOB_THROUGHPUT only increments on completed/failed) —
    processing_queue_depth is a gauge kept current from the database by the
    API process itself (app/main.py's _poll_queue_depth), not a counter.

Usage:
    cd backend
    python scripts/load_test.py --concurrency 4 --iterations 3
"""

import argparse
import asyncio
import pathlib
import sys
import time
import uuid

import httpx

API = "http://localhost:8000/api/v1"
CV_PATH = pathlib.Path(__file__).resolve().parents[2] / "Test Cvs" / "Tobiloba_Odu_CV.pdf"

JOB_TEXT = (
    "Senior Product Designer\n\nRequirements:\n- Figma\n- UX research\n- "
    "usability testing\n- wireframing\n- design systems\n- accessibility, WCAG 2.1"
)


async def _poll(client: httpx.AsyncClient, path: str, timeout: float, headers: dict) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await client.get(f"{API}{path}", headers=headers)
        if r.status_code != 200:
            return {"status": "error", "code": r.status_code}
        body = r.json()
        if body.get("status") in ("completed", "failed"):
            return body
        await asyncio.sleep(2)
    return {"status": "timeout"}


async def worker(worker_id: int, iterations: int, client: httpx.AsyncClient):
    results = []
    for i in range(iterations):
        t0 = time.monotonic()
        try:
            # 1. anonymous trial session (no account needed)
            r = await client.post(f"{API}/trial-sessions")
            if r.status_code != 201:
                results.append(("trial_session_http", r.status_code))
                continue
            headers = {"X-Trial-Session-Id": r.json()["trialSessionId"]}

            # 2. upload CV
            with open(CV_PATH, "rb") as fh:
                r = await client.post(
                    f"{API}/cvs",
                    headers=headers,
                    files={"file": ("Tobiloba_Odu_CV.pdf", fh, "application/pdf")},
                )
            if r.status_code != 202:
                results.append(("upload_http", r.status_code))
                continue
            cv_id = r.json()["cvId"]

            # 3. submit job text
            r = await client.post(
                f"{API}/job-posts/text", headers=headers, json={"text": JOB_TEXT}
            )
            if r.status_code != 202:
                results.append(("job_post_http", r.status_code))
                continue
            job_post_id = r.json()["jobPostId"]
            job_id = r.json()["processingJobId"]

            # 4. poll the job-post structuring job until terminal — this is
            #    where a silent drop would show up (never reaches terminal).
            status = await _poll(client, f"/jobs/{job_id}", 120, headers)
            results.append(("job_post", status.get("status", "unknown")))
        except httpx.HTTPError as e:
            results.append(("exception", type(e).__name__))
        results.append(("wall_seconds", round(time.monotonic() - t0, 1)))
    return worker_id, results


async def main(concurrency: int, iterations: int) -> int:
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        tasks = [worker(w, iterations, client) for w in range(concurrency)]
        outcomes = await asyncio.gather(*tasks)

    print(f"\n=== load-test results (concurrency={concurrency}, iterations={iterations}) ===")
    total = 0
    dropped = 0
    by_status = {}
    for worker_id, results in outcomes:
        statuses = [r for r in results if isinstance(r, tuple) and r[0] != "wall_seconds"]
        for tag, val in statuses:
            total += 1
            by_status[val] = by_status.get(val, 0) + 1
            if val in ("timeout", "error", "exception"):
                dropped += 1
        print(f"  worker {worker_id}: {results}")

    print(f"\n  total operations: {total}")
    print(f"  dropped/error/timeout: {dropped}")
    print(f"  breakdown: {by_status}")

    # Read the API /metrics for processing_queue_depth so the queue-depth
    # alert can be checked against real load.
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            # /metrics (no trailing slash) 307-redirects to /metrics/ (the
            # mounted ASGI app) — httpx doesn't follow redirects by default,
            # which silently produced an empty body here before.
            r = await client.get("http://localhost:8000/metrics")
            lines = [ln for ln in r.text.splitlines() if ln.startswith("processing_queue_depth")]
            print("\n  processing_queue_depth (from /metrics):")
            for ln in lines:
                print(f"    {ln}")
        except httpx.HTTPError as e:
            print(f"\n  (could not read /metrics: {e})")

    print("\nNOTE: the queue-depth alert (prometheus/alert_rules.yml) fires on")
    print("sum by (job_type) (processing_queue_depth) > 5 for 10m — compare the")
    print("gauge values above during/after this run to confirm whether it fired,")
    print("or check Alertmanager /api/v2/alerts.")
    return 1 if dropped else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--iterations", type=int, default=3)
    args = p.parse_args()
    sys.exit(asyncio.run(main(args.concurrency, args.iterations)))
