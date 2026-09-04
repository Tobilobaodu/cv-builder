// Scenario A: browse and inspect jobs — authenticate, list job posts,
// list matches, open a detail record. Read-only, no queue/worker load.
//
// Run: k6 run -e BASE_URL=https://staging.example -e TEST_EMAIL=... -e TEST_PASSWORD=... scenario_a_browse.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";
import { login, authHeaders } from "./lib/auth.js";

const listLatency = new Trend("job_post_list_latency");
const detailLatency = new Trend("job_post_detail_latency");

export const options = {
  scenarios: {
    browse: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "2m", target: 10 },
        { duration: "10m", target: 50 },
        { duration: "10m", target: 100 },
        { duration: "2m", target: 0 },
      ],
      gracefulRampDown: "30s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    job_post_list_latency: ["p(95)<500"],
    job_post_detail_latency: ["p(95)<300"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

// Authenticate once in setup(), not per iteration in default(): the auth
// tier's rate limit (5 requests/60s per IP — app/core/config.py) applies
// to every VU sharing this run's one source IP, so calling login() inside
// the per-iteration loop rate-limits almost every VU into 429s the moment
// concurrency rises above a handful — confirmed live: a 30-VU run against
// local Docker saw 99% of iterations fail at the login step alone, never
// reaching the endpoints this scenario actually measures. One token,
// reused by every VU for the run's duration, is both correct (one real
// seeded test account, per lib/auth.js's own docstring) and avoids this
// entirely.
export function setup() {
  const token = login(BASE_URL, __ENV.TEST_EMAIL, __ENV.TEST_PASSWORD);
  if (!token) {
    throw new Error("setup() login failed — check TEST_EMAIL/TEST_PASSWORD and that the account exists.");
  }
  return { token };
}

export default function (data) {
  const headers = authHeaders(data.token);

  const listRes = http.get(`${BASE_URL}/api/v1/job-posts?limit=25&offset=0`, {
    headers, tags: { endpoint: "job_posts_list" },
  });
  listLatency.add(listRes.timings.duration);
  // Every list endpoint in this API returns {items, total, limit, offset},
  // never a bare array (confirmed live against /job-posts, /matches, /cvs)
  // — this script's original Array.isArray()/`.length` checks against
  // listRes.json() directly would always have failed, silently skipping
  // the detail-fetch branch below even with real data present. Exactly
  // the "reviewed but unexecuted" risk the loadtest README already flags.
  const ok = check(listRes, {
    "list status 200": (r) => r.status === 200,
    "list has items array": (r) => Array.isArray(r.json("items")),
  });

  if (ok && listRes.json("items").length > 0) {
    const jobPostId = listRes.json("items")[0].id;
    const detailRes = http.get(`${BASE_URL}/api/v1/job-posts/${jobPostId}`, {
      headers, tags: { endpoint: "job_post_detail" },
    });
    detailLatency.add(detailRes.timings.duration);
    check(detailRes, { "detail status 200": (r) => r.status === 200 });
  }

  const matchesRes = http.get(`${BASE_URL}/api/v1/matches?limit=25&offset=0`, {
    headers, tags: { endpoint: "matches_list" },
  });
  check(matchesRes, { "matches status 200": (r) => r.status === 200 });

  sleep(Math.random() * 2 + 1);
}
