"use client"

import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { errorMessage } from "@/lib/api"
import { listCvs } from "@/lib/dashboard-api"
import {
  createJobPostCollection,
  triggerCoverageReport,
  getCoverageReport,
} from "@/lib/trial-api"
import { useJobPoll } from "@/hooks/use-job-poll"
import { Tag } from "@/components/modernist/tag"
import { ProgressBar } from "@/components/modernist/progress-bar"

/**
 * Sprint 5's multi-job-post coverage reporting (job-post-collections +
 * coverage-reports) restyled to Modernist per the Jobs-screen mockup's
 * "coverage report" panel — same data flow as before (pick a CV, create a
 * collection from the selected job posts, trigger the report, poll, render
 * aggregate gaps), reusing useJobPoll (ProcessingJobRef) like ATS-check.
 */
export function CoverageReportPanel({
  selectedJobPostIds,
  onClearSelection,
}: {
  selectedJobPostIds: string[]
  onClearSelection: () => void
}) {
  const queryClient = useQueryClient()
  const [cvId, setCvId] = useState<string>("")
  const [jobId, setJobId] = useState<string | null>(null)
  const [isStarting, setIsStarting] = useState(false)
  const { job, isCompleted, isFailed } = useJobPoll(jobId)

  const cvsQuery = useQuery({ queryKey: ["dashboard-cvs-for-coverage"], queryFn: () => listCvs() })

  // A completed report runs _get_or_run_match for any selected job that had
  // no existing MatchRun, creating a fresh one as a side effect — the Jobs
  // table's own "Match" column reads from a separately-cached ["dashboard-
  // matches"] query that has no way to know that happened, so without this
  // it kept showing stale/empty scores until a manual page reload.
  useEffect(() => {
    if (isCompleted) {
      void queryClient.invalidateQueries({ queryKey: ["dashboard-matches"] })
    }
  }, [isCompleted, queryClient])

  // POST .../coverage-report only returns a ProcessingJobRef, not the
  // report's own id — but worker_jobs.py creates that ProcessingJob with
  // source_entity_type="coverage_report", source_entity_id=report.id, and
  // GET /jobs/{jobId} exposes sourceEntityId, so the completed job's own
  // status response *is* the report id, no separate lookup needed.
  const reportId = job?.sourceEntityType === "coverage_report" ? job.sourceEntityId : null

  const reportQuery = useQuery({
    queryKey: ["coverage-report", reportId],
    queryFn: () => getCoverageReport(reportId as string),
    enabled: !!reportId && isCompleted,
  })

  async function handleRun() {
    if (!cvId || selectedJobPostIds.length === 0) return
    setIsStarting(true)
    try {
      const collection = await createJobPostCollection(
        `Coverage report ${new Date().toLocaleString()}`,
        selectedJobPostIds
      )
      const job = await triggerCoverageReport(collection.id, cvId)
      setJobId(job.jobId)
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't start the coverage report."))
    } finally {
      setIsStarting(false)
    }
  }

  if (selectedJobPostIds.length === 0) return null

  return (
    <section style={{ background: "var(--color-surface)", padding: "28px 32px" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 24, marginBottom: 20 }}>
        <h3 style={{ fontSize: 20, margin: 0 }}>
          COVERAGE REPORT — {selectedJobPostIds.length} JOB{selectedJobPostIds.length === 1 ? "" : "S"} SELECTED
        </h3>
        <button type="button" className="btn btn-ghost" onClick={onClearSelection}>
          Clear selection
        </button>
      </div>

      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 24, flexWrap: "wrap" }}>
        <div className="field" style={{ width: 280 }}>
          <label>Compare against</label>
          <select className="input" value={cvId} onChange={(e) => setCvId(e.target.value)}>
            <option value="">Select a CV…</option>
            {cvsQuery.data?.items.map((cv) => (
              <option key={cv.id} value={cv.id}>
                {cv.originalFilename}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleRun}
          disabled={!cvId || isStarting || !!jobId}
        >
          {isStarting ? "Starting…" : "Run coverage report"}
        </button>
      </div>

      {jobId && !isCompleted && !isFailed && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <p style={{ fontSize: 13, color: "var(--color-neutral-700)", margin: 0 }}>Running report across selected jobs…</p>
          <ProgressBar isActive width={240} expectedDurationMs={Math.max(6000, selectedJobPostIds.length * 6000)} />
        </div>
      )}
      {isFailed && <p style={{ fontSize: 13, color: "var(--color-accent-700)" }}>The report failed. Please try again.</p>}
      {isCompleted && reportQuery.isLoading && (
        <p style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>Loading results…</p>
      )}
      {reportQuery.data && (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {reportQuery.data.aggregateGaps.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>No recurring gaps found.</p>
          ) : (
            reportQuery.data.aggregateGaps.map((gap, i, arr) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 20,
                  padding: "12px 0",
                  borderTop: "1px solid var(--color-divider)",
                  borderBottom: i === arr.length - 1 ? "1px solid var(--color-divider)" : undefined,
                }}
              >
                <div style={{ fontSize: 14 }}>{gap.requirementTextCluster}</div>
                <Tag variant={gap.recurrenceCount === selectedJobPostIds.length ? "accent" : "neutral"}>
                  {gap.recurrenceCount} of {selectedJobPostIds.length} jobs
                </Tag>
              </div>
            ))
          )}
        </div>
      )}
    </section>
  )
}
