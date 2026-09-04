import { useQuery } from "@tanstack/react-query"
import { getJob, type ProcessingJob } from "@/lib/trial-api"

const TERMINAL_STATUSES = new Set(["completed", "failed"])
const POLL_FAST_MS = 1000
const POLL_SLOW_MS = 5000
// First 30s of a job's life polled at POLL_FAST_MS, then back off. Most
// jobs here (text extraction, LLM analysis) finish in single-digit to
// low-double-digit seconds now that the Docling/Textract pipeline this
// backoff was originally tuned for (tens of seconds to minutes) is
// decommissioned — see worker_jobs.py's top-of-file comment. A job
// finishing at 12s under the old 2/4/8/15s backoff wasn't shown until
// 14s; one finishing at 16s waited until 29s. jbs-solution-sheet.md S5.
const FAST_PHASE_POLLS = 30

/**
 * Polls GET /jobs/{jobId} — the backend's single source of truth for async
 * job status (see app/api/v1/jobs.py) — until it reaches a terminal state.
 * Pass jobId=null to skip polling (e.g. before the job has been created).
 */
export function useJobPoll(jobId: string | null) {
  const query = useQuery<ProcessingJob>({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (q) => {
      const status = q.state.data?.status
      if (status && TERMINAL_STATUSES.has(status)) return false
      return q.state.dataUpdateCount < FAST_PHASE_POLLS ? POLL_FAST_MS : POLL_SLOW_MS
    },
  })

  return {
    job: query.data,
    isPolling: jobId !== null && !TERMINAL_STATUSES.has(query.data?.status ?? ""),
    isCompleted: query.data?.status === "completed",
    isFailed: query.data?.status === "failed",
    error: query.error,
  }
}
