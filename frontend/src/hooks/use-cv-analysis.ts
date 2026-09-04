import { useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ApiError } from "@/lib/api"
import { getCvAnalysis, triggerCvAnalysis, type CvAnalysis } from "@/lib/trial-api"
import { useJobPoll } from "@/hooks/use-job-poll"

/**
 * GET /cvs/{id}/analysis 404s until an analysis has run. Rather than
 * requiring a manual "run analysis" button (the mockup has none — CVs that
 * aren't scored yet just show "Scoring…"), this auto-triggers the POST on
 * first 404 and polls the resulting job via useJobPoll, the same
 * trigger -> poll -> refetch shape as AtsCheckDialog/CoverageReportPanel.
 */
export function useCvAnalysis(cvId: string | null) {
  const [jobId, setJobId] = useState<string | null>(null)
  const triggeredRef = useRef(false)
  const { isCompleted, isFailed } = useJobPoll(jobId)

  useEffect(() => {
    triggeredRef.current = false
    // Reset the in-flight trigger job whenever the CV changes — this
    // mirrors use-require-auth.ts's precedent for a deliberate
    // synchronous reset tied to a prop change, not a derived-state
    // anti-pattern.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setJobId(null)
  }, [cvId])

  const query = useQuery<CvAnalysis>({
    queryKey: ["cv-analysis", cvId, isCompleted],
    queryFn: () => getCvAnalysis(cvId as string),
    enabled: !!cvId,
    retry: false,
  })

  useEffect(() => {
    if (!cvId) return
    const notFound = query.isError && query.error instanceof ApiError && query.error.status === 404
    if (notFound && !triggeredRef.current && !jobId) {
      triggeredRef.current = true
      triggerCvAnalysis(cvId)
        .then((job) => setJobId(job.jobId))
        .catch(() => {
          triggeredRef.current = false
        })
    }
  }, [cvId, jobId, query.isError, query.error])

  const isScoring = !!cvId && !query.data && !isFailed && (query.isLoading || !!jobId)

  return {
    analysis: query.data,
    isScoring,
    isFailed,
  }
}
