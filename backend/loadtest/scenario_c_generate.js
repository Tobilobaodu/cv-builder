// Scenario C: generate a tailored application for a CV/job pair that
// already exists (match -> tailored CV -> export). Requires seeded data —
// a parsed CvProfileVersion and a parsed JobPost owned by TEST_EMAIL — this
// script does not upload/parse them itself (that's Scenario B's job, and
// mixing the two into one VU loop would conflate upload latency with
// generation latency in the results).
//
// Deliberately excludes the cover-letter flow: /cover-letters/start ->
// /answers -> /regenerate is an interactive Q&A sequence, not a fire-and-
// poll job, so it doesn't fit this loop's shape without either faking
// answers (invalidating the latency numbers) or a separate, dedicated
// script — left as a follow-up, not silently approximated here.
//
// Run: k6 run -e BASE_URL=... -e TEST_EMAIL=... -e TEST_PASSWORD=... \
//        -e CV_PROFILE_VERSION_ID=... -e JOB_POST_ID=... scenario_c_generate.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";
import { login, authHeaders } from "./lib/auth.js";

const matchStageLatency = new Trend("match_stage_seconds");
const tailoredCvStageLatency = new Trend("tailored_cv_stage_seconds");
const exportStageLatency = new Trend("export_stage_seconds");

export const options = {
  scenarios: {
    generate: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "2m", target: 5 },
        { duration: "10m", target: 20 },
        { duration: "2m", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.02"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

function pollJob(baseUrl, headers, jobId, capMs) {
  const startedAt = Date.now();
  let status = "queued";
  while (!["completed", "failed"].includes(status)) {
    sleep(3);
    const res = http.get(`${baseUrl}/api/v1/jobs/${jobId}`, { headers, tags: { endpoint: "job_status_poll" } });
    if (res.status !== 200) break;
    status = res.json("status");
    if (Date.now() - startedAt > capMs) break;
  }
  return { status, elapsedSeconds: (Date.now() - startedAt) / 1000 };
}

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
  const headers = authHeaders(data.token);

  // Stage 1: match
  const matchRes = http.post(
    `${BASE_URL}/api/v1/matches`,
    JSON.stringify({ cvProfileVersionId: __ENV.CV_PROFILE_VERSION_ID, jobPostId: __ENV.JOB_POST_ID }),
    { headers, tags: { endpoint: "match_create" } },
  );
  if (!check(matchRes, { "match accepted (202)": (r) => r.status === 202 })) return;
  const matchId = matchRes.json("matchId");
  const matchResult = pollJob(BASE_URL, headers, matchRes.json("processingJobId"), 3 * 60 * 1000);
  matchStageLatency.add(matchResult.elapsedSeconds);
  if (matchResult.status !== "completed") return;

  // Stage 2: tailored CV
  const tailoredRes = http.post(
    `${BASE_URL}/api/v1/matches/${matchId}/tailored-cv`,
    null,
    { headers, tags: { endpoint: "tailored_cv_create" } },
  );
  if (!check(tailoredRes, { "tailored-cv accepted (202)": (r) => r.status === 202 })) return;
  const draftId = tailoredRes.json("jobId"); // ProcessingJobRef only — draft id comes from GET /tailored-cvs polling in the real UI
  const tailoredResult = pollJob(BASE_URL, headers, tailoredRes.json("jobId"), 5 * 60 * 1000);
  tailoredCvStageLatency.add(tailoredResult.elapsedSeconds);
  if (tailoredResult.status !== "completed") return;

  // Stage 3: export (draftId here is actually the ProcessingJob's
  // sourceEntityId once completed — see GET /jobs/{jobId}, same pattern
  // frontend/src/components/coverage-report-panel.tsx uses)
  const jobDetailRes = http.get(`${BASE_URL}/api/v1/jobs/${tailoredRes.json("jobId")}`, { headers });
  const tailoredCvDraftId = jobDetailRes.json("sourceEntityId");
  const exportRes = http.post(
    `${BASE_URL}/api/v1/exports/cv/${tailoredCvDraftId}`,
    JSON.stringify({ format: "docx" }),
    { headers, tags: { endpoint: "export_create" } },
  );
  if (!check(exportRes, { "export accepted (202)": (r) => r.status === 202 })) return;
  const exportStart = Date.now();
  let exportStatus = "queued";
  while (!["completed", "failed"].includes(exportStatus)) {
    sleep(2);
    const res = http.get(`${BASE_URL}/api/v1/exports/${exportRes.json("id")}`, { headers, tags: { endpoint: "export_poll" } });
    if (res.status !== 200) break;
    exportStatus = res.json("status");
    if (Date.now() - exportStart > 5 * 60 * 1000) break;
  }
  exportStageLatency.add((Date.now() - exportStart) / 1000);
  check(exportStatus, { "export completed": (v) => v === "completed" });
}
