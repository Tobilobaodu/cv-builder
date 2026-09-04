// Scenario B: upload a CV, poll processing status through the real
// docling -> textract -> merge -> cv_parse pipeline (app/workers/worker_jobs.py).
//
// Run: k6 run -e BASE_URL=... -e TEST_EMAIL=... -e TEST_PASSWORD=... \
//        -e FIXTURE_CV_PATH=./fixtures/sample-cv.pdf scenario_b_upload.js
//
// FIXTURE_CV_PATH must point at a real PDF/DOCX — none is committed to
// this repo (see README.md#fixtures for why).
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";
import { login, authHeaders } from "./lib/auth.js";

const uploadAckLatency = new Trend("upload_ack_latency");
const endToEndLatency = new Trend("cv_processing_end_to_end_seconds");

export const options = {
  scenarios: {
    upload: {
      executor: "constant-arrival-rate",
      rate: 2,
      timeUnit: "1s",
      duration: "10m",
      preAllocatedVUs: 20,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    upload_ack_latency: ["p(95)<1000"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const FIXTURE_CV_PATH = __ENV.FIXTURE_CV_PATH || "./fixtures/sample-cv.pdf";
const cvFile = open(FIXTURE_CV_PATH, "b");

// See scenario_a_browse.js's setup() comment: logging in per-iteration
// instead of once rate-limits almost every VU into 429s under any real
// concurrency (auth tier: 5 requests/60s per IP) — confirmed live.
export function setup() {
  const token = login(BASE_URL, __ENV.TEST_EMAIL, __ENV.TEST_PASSWORD);
  if (!token) {
    throw new Error("setup() login failed — check TEST_EMAIL/TEST_PASSWORD and that the account exists.");
  }
  return { token };
}

export default function (data) {
  const token = data.token;

  const uploadRes = http.post(
    `${BASE_URL}/api/v1/cvs`,
    { file: http.file(cvFile, "cv.pdf", "application/pdf") },
    { headers: { Authorization: `Bearer ${token}` }, tags: { endpoint: "cv_upload" } },
  );
  uploadAckLatency.add(uploadRes.timings.duration);
  const accepted = check(uploadRes, { "upload accepted (202)": (r) => r.status === 202 });
  if (!accepted) return;

  const jobId = uploadRes.json("processingJobId");
  const startedAt = Date.now();
  const headers = authHeaders(token);
  let status = "queued";

  // Matches the doc's "avoid polling too aggressively" guidance — fixed
  // 3s interval here rather than the tighter loops in try/results/page.tsx's
  // UI polling (that one runs against a single browser tab, not N VUs).
  while (!["completed", "failed"].includes(status)) {
    sleep(3);
    const jobRes = http.get(`${BASE_URL}/api/v1/jobs/${jobId}`, {
      headers, tags: { endpoint: "job_status_poll" },
    });
    if (jobRes.status !== 200) break;
    status = jobRes.json("status");

    if (Date.now() - startedAt > 5 * 60 * 1000) break; // 5 min cap, matches CV-extraction failure threshold
  }

  endToEndLatency.add((Date.now() - startedAt) / 1000);
  check(status, { "processing completed": (v) => v === "completed" });
}
