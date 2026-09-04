"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Trash2 } from "lucide-react"
import { deleteCv, listCvs, listMatches } from "@/lib/dashboard-api"
import { errorMessage } from "@/lib/api"
import { ScoreBar } from "@/components/modernist/score-bar"
import { Tag } from "@/components/modernist/tag"
import { TableShell } from "@/components/modernist/table-shell"

const STATUS_LABEL: Record<string, string> = {
  parsed: "Parsed",
  completed: "Parsed",
  processing: "Extracting",
  pending: "Extracting",
  failed: "Failed",
  deleted: "Deleted",
}

export default function CvsPage() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ["dashboard-cvs"], queryFn: () => listCvs() })
  // GET /matches/{id} doesn't expose which CV it ran against, so only the
  // most-recently-uploaded CV (items[0]) can be reliably linked to a
  // report — the most recent match overall. Older CVs hide the Report
  // link rather than guess which match used them (see final report).
  const matchesQuery = useQuery({ queryKey: ["dashboard-matches"], queryFn: () => listMatches() })
  const latestMatch = matchesQuery.data?.items[0]
  const [deletingId, setDeletingId] = useState<string | null>(null)

  async function handleDelete(cvId: string, filename: string) {
    if (!window.confirm(`Delete ${filename}? This can't be undone.`)) return
    setDeletingId(cvId)
    try {
      await deleteCv(cvId)
      void queryClient.invalidateQueries({ queryKey: ["dashboard-cvs"] })
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't delete that CV."))
    } finally {
      setDeletingId(null)
    }
  }

  const items = query.data?.items ?? []

  return (
    <div style={{ padding: 48, display: "flex", flexDirection: "column", gap: 32, maxWidth: 1100 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 32 }}>
        <div>
          <h1 style={{ fontSize: 42, margin: "0 0 8px" }}>CVS</h1>
          <p style={{ margin: 0, fontSize: 15, color: "var(--color-neutral-700)" }}>
            {items.length > 0
              ? `${items.length} file${items.length === 1 ? "" : "s"}. The most recent one is used for new matches.`
              : "Upload a CV to get started."}
          </p>
        </div>
        <Link href="/dashboard/new" className="btn btn-primary" style={{ flex: "none" }}>
          Upload a CV
        </Link>
      </div>

      <TableShell
        isLoading={query.isLoading}
        isError={query.isError}
        isEmpty={!!query.data && query.data.items.length === 0}
        emptyMessage="You haven't uploaded a CV yet — upload one to get started."
      >
        <thead>
          <tr>
            <th>Filename</th>
            <th>Status</th>
            <th style={{ width: 190 }}>Resume score</th>
            <th>Issues</th>
            <th>Uploaded</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((cv, index) => {
            const isCurrent = index === 0
            const reportHref = isCurrent && latestMatch ? `/dashboard/matches/${latestMatch.id}` : null
            return (
              <tr key={cv.id}>
                <td style={{ fontWeight: 600 }}>
                  {cv.originalFilename}
                  {isCurrent && (
                    <Tag variant="accent" className="ml-2">
                      Current
                    </Tag>
                  )}
                </td>
                <td>
                  <Tag variant={cv.status === "failed" ? "outline" : "neutral"}>
                    {STATUS_LABEL[cv.status] ?? cv.status}
                  </Tag>
                </td>
                <td>
                  <ScoreBar score={cv.resumeScore ?? null} size="sm" />
                </td>
                <td style={{ color: cv.issueCount ? "var(--color-accent-700)" : "var(--color-neutral-600)", fontWeight: cv.issueCount ? 600 : 400 }}>
                  {cv.issueCount ?? "—"}
                </td>
                <td style={{ color: "var(--color-neutral-700)" }}>
                  {new Date(cv.createdAt).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
                </td>
                <td style={{ textAlign: "right", display: "flex", gap: 8, justifyContent: "flex-end", alignItems: "center" }}>
                  {reportHref && (
                    <Link href={reportHref} className="btn btn-ghost">
                      Report
                    </Link>
                  )}
                  <button
                    type="button"
                    className="btn btn-ghost"
                    aria-label={`Delete ${cv.originalFilename}`}
                    disabled={deletingId === cv.id}
                    onClick={() => handleDelete(cv.id, cv.originalFilename)}
                  >
                    <Trash2 width={16} height={16} strokeWidth={2} />
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </TableShell>
    </div>
  )
}
