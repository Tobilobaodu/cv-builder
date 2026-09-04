// Scenario D: concurrent generation burst — many VUs request matching
// within a short window to observe queue depth, worker saturation, and
// retry/dead-letter rate under load, per the addendum's §2.5 Scenario D.
// Deliberately request-only (no polling loop) — the burst is about publish
// throughput and queue absorption, not end-to-end latency (that's what
// Scenario C measures at steady state).
//
// Watch during the run: processing_queue_depth (Prometheus gauge, this
// repo's app/core/metrics.py), worker CPU/memory, and the
// processing_jobs_total{status="failed"} rate — a spike there under burst
// load without a matching spike in real upstream errors would indicate
// worker capacity, not correctness, is the bottleneck.
//
// Run: k6 run -e BASE_URL=... -e TEST_EMAIL=... -e TEST_PASSWORD=... \
//        -e CV_PROFILE_VERSION_ID=... -e JOB_POST_ID=... scenario_d_burst.js
import http from "k6/http";
import { check } from "k6";
import { login, authHeaders } from "./lib/auth.js";

export const options = {
  scenarios: {
    match_burst: {
      executor: "constant-arrival-rate",
      rate: 20,
      timeUnit: "1s",
      duration: "3m",
      preAllocatedVUs: 50,
      maxVUs: 200,
    },
  },
  thresholds: {
    // Looser than Scenario A/C — a burst test's job is to find where this
    // breaks, not to assert it doesn't (see doc §2.6, "Stress" test type).
    http_req_failed: ["rate<0.05"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

// See scenario_a_browse.js's setup() comment: logging in per-iteration
// instead of once rate-limits almost every VU into 429s under any real
// concurrency (auth tier: 5 requests/60s per IP) — confirmed live. This
// scenario is especially exposed to it: at rate=20/s, per-iteration login
// would burn the whole auth budget in well under a second.
export function setup() {
  const token = login(BASE_URL, __ENV.TEST_EMAIL, __ENV.TEST_PASSWORD);
  if (!token) {
    throw new Error("setup() login failed — check TEST_EMAIL/TEST_PASSWORD and that the account exists.");
  }
  return { token };
}

export default function (data) {
  const headers = authHeaders(data.token);

  const res = http.post(
    `${BASE_URL}/api/v1/matches`,
    JSON.stringify({ cvProfileVersionId: __ENV.CV_PROFILE_VERSION_ID, jobPostId: __ENV.JOB_POST_ID }),
    { headers, tags: { endpoint: "match_create" } },
  );
  check(res, {
    "match accepted (202)": (r) => r.status === 202,
    // A 429 here is enforce_concurrent_job_limit doing its job, not a
    // failure of the system under test — track separately from real errors.
    "not a 5xx": (r) => r.status < 500,
  });
}
