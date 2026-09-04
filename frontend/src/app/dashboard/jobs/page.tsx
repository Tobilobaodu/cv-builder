"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Trash2 } from "lucide-react"

import { deleteJobPost, listJobPosts, listMatches } from "@/lib/dashboard-api"
import { submitJobPostText } from "@/lib/trial-api"
import { errorMessage } from "@/lib/api"
import { ScoreBar } from "@/components/modernist/score-bar"
import { Tag } from "@/components/modernist/tag"
import { TableShell } from "@/components/modernist/table-shell"
import { CoverageReportPanel } from "@/components/coverage-report-panel"

const STATUS_LABEL: Record<string, string> = {
  completed: "Completed",
  processing: "Parsing",
  pending: "Parsing",
  failed: "Failed",
}

const PAGE_SIZE = 20

export default function JobsPage() {
  const queryClient = useQueryClient()
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)
  const query = useQuery({
    queryKey: ["dashboard-job-posts", visibleCount],
    queryFn: () => listJobPosts(visibleCount, 0),
  })
  // Kept in step with visibleCount — otherwise a job loaded past the first
  // page would never find its score, since matchesQuery would still only
  // hold the first PAGE_SIZE matches.
  const matchesQuery = useQuery({
    queryKey: ["dashboard-matches", visibleCount],
    queryFn: () => listMatches(visibleCount, 0),
  })
  const [selected, setSelected] = useState<string[]>([])
  const [pastingJobId, setPastingJobId] = useState<string | null>(null)
  const [pasteText, setPasteText] = useState("")
  const [isSubmittingPaste, setIsSubmittingPaste] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  function toggle(id: string) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  async function handleDelete(jobPostId: string, label: string) {
    if (!window.confirm(`Delete ${label}? This can't be undone.`)) return
    setDeletingId(jobPostId)
    try {
      await deleteJobPost(jobPostId)
      setSelected((prev) => prev.filter((id) => id !== jobPostId))
      void queryClient.invalidateQueries({ queryKey: ["dashboard-job-posts"] })
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't delete that job."))
    } finally {
      setDeletingId(null)
    }
  }

  function scoreForJobPost(jobPostId: string): number | null {
    const match = matchesQuery.data?.items.find((m) => m.jobPostId === jobPostId)
    return match?.score ?? null
  }

  // The failed posting can't be retried in place — there's no PATCH/retry
  // endpoint for an existing job post — so this creates a fresh
  // text-sourced job post from the pasted content, the same call
  // /dashboard/new's "paste description" path uses.
  async function handlePasteSubmit() {
    if (pasteText.trim().length < 100) {
      toast.error("Paste at least 100 characters of the job description.")
      return
    }
    setIsSubmittingPaste(true)
    try {
      await submitJobPostText(pasteText.trim())
      toast.success("Job description submitted.")
      setPastingJobId(null)
      setPasteText("")
      void queryClient.invalidateQueries({ queryKey: ["dashboard-job-posts"] })
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't submit that description."))
    } finally {
      setIsSubmittingPaste(false)
    }
  }

  const items = query.data?.items ?? []

  return (
    <div style={{ padding: 48, display: "flex", flexDirection: "column", gap: 32, maxWidth: 1100 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 32 }}>
        <div>
          <h1 style={{ fontSize: 42, margin: "0 0 8px" }}>JOBS</h1>
          <p style={{ margin: 0, fontSize: 15, color: "var(--color-neutral-700)" }}>
            Select two or more to see what they ask for that your CV doesn&apos;t cover.
          </p>
        </div>
        <Link href="/dashboard/new" className="btn btn-primary" style={{ flex: "none" }}>
          Add a job
        </Link>
      </div>

      <TableShell
        isLoading={query.isLoading}
        isError={query.isError}
        isEmpty={!!query.data && query.data.items.length === 0}
        emptyMessage="You haven't saved any jobs yet — paste a job link or description to get started."
      >
        <thead>
          <tr>
            <th style={{ width: 36 }}></th>
            <th>Role</th>
            <th>Employer</th>
            <th style={{ width: 170 }}>Match</th>
            <th>Status</th>
            <th>Added</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((job) => {
            const failed = job.status === "failed"
            const isPasting = pastingJobId === job.id
            return (
              <tr key={job.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.includes(job.id)}
                    onChange={() => toggle(job.id)}
                    disabled={failed}
                    aria-label={`Select ${job.profile?.jobTitle ?? "job"}`}
                    style={{ accentColor: "var(--color-accent)", width: 15, height: 15 }}
                  />
                </td>
                <td style={{ fontWeight: 600, color: failed ? "var(--color-neutral-600)" : undefined }}>
                  {failed ? "Couldn't read this posting" : (job.profile?.jobTitle ?? "—")}
                </td>
                <td style={{ color: failed ? "var(--color-neutral-600)" : undefined }}>
                  {failed ? "—" : (job.profile?.employer ?? "—")}
                </td>
                <td>
                  {failed ? (
                    isPasting ? null : (
                      <button type="button" className="btn btn-ghost" onClick={() => setPastingJobId(job.id)}>
                        Paste the text instead
                      </button>
                    )
                  ) : (
                    <ScoreBar score={scoreForJobPost(job.id)} size="sm" width={80} />
                  )}
                </td>
                <td>
                  <Tag variant={failed ? "outline" : job.status === "completed" ? "neutral" : "accent"}>
                    {STATUS_LABEL[job.status] ?? job.status}
                  </Tag>
                </td>
                <td style={{ color: "var(--color-neutral-700)" }}>
                  {new Date(job.createdAt).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
                </td>
                <td style={{ textAlign: "right" }}>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    aria-label={`Delete ${job.profile?.jobTitle ?? "job"}`}
                    disabled={deletingId === job.id}
                    onClick={() => handleDelete(job.id, job.profile?.jobTitle ?? "this job")}
                  >
                    <Trash2 width={16} height={16} strokeWidth={2} />
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </TableShell>

      {query.data && query.data.total > items.length && (
        <div style={{ display: "flex", justifyContent: "center" }}>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={query.isFetching}
            onClick={() => setVisibleCount((n) => n + PAGE_SIZE)}
          >
            {query.isFetching ? "Loading…" : "Load more"}
          </button>
        </div>
      )}

      {pastingJobId && (
        <section style={{ background: "var(--color-surface)", padding: 24 }}>
          <div className="field" style={{ marginBottom: 12 }}>
            <label>Job description</label>
            <textarea
              className="input"
              style={{ minHeight: 140 }}
              placeholder="Paste the posting text here…"
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
            />
          </div>
          <div style={{ display: "flex", gap: 12 }}>
            <button
              type="button"
              className="btn btn-primary"
              disabled={isSubmittingPaste}
              onClick={handlePasteSubmit}
            >
              {isSubmittingPaste ? "Submitting…" : "Submit"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                setPastingJobId(null)
                setPasteText("")
              }}
            >
              Cancel
            </button>
          </div>
        </section>
      )}

      <CoverageReportPanel selectedJobPostIds={selected} onClearSelection={() => setSelected([])} />
    </div>
  )
}
