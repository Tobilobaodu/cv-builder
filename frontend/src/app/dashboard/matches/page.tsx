"use client"

import { useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Trash2 } from "lucide-react"
import { deleteMatch, listMatches } from "@/lib/dashboard-api"
import { errorMessage } from "@/lib/api"
import { ScoreBar } from "@/components/modernist/score-bar"
import { Tag } from "@/components/modernist/tag"
import { TableShell } from "@/components/modernist/table-shell"

const STATUS_LABEL: Record<string, string> = {
  completed: "Tailored",
  processing: "In progress",
  pending: "In progress",
  failed: "Failed",
}

export default function MatchesPage() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ["dashboard-matches"], queryFn: () => listMatches() })
  const [deletingId, setDeletingId] = useState<string | null>(null)

  async function handleDelete(matchId: string, label: string) {
    if (!window.confirm(`Delete the report for ${label}? This can't be undone.`)) return
    setDeletingId(matchId)
    try {
      await deleteMatch(matchId)
      void queryClient.invalidateQueries({ queryKey: ["dashboard-matches"] })
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't delete that report."))
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div style={{ padding: 48, display: "flex", flexDirection: "column", gap: 32, maxWidth: 1100 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 32 }}>
        <div>
          <h1 style={{ fontSize: 42, margin: "0 0 8px" }}>REPORTS</h1>
          <p style={{ margin: 0, fontSize: 15, color: "var(--color-neutral-700)" }}>
            Every match you&apos;ve run, newest first.
          </p>
        </div>
        <Link href="/dashboard/new" className="btn btn-primary" style={{ flex: "none" }}>
          New match
        </Link>
      </div>

      <TableShell
        isLoading={query.isLoading}
        isError={query.isError}
        isEmpty={!!query.data && query.data.items.length === 0}
        emptyMessage="No matches yet — upload a CV and a job to see how well they fit."
      >
        <thead>
          <tr>
            <th>Role</th>
            <th>Employer</th>
            <th style={{ width: 170 }}>Match</th>
            <th>Status</th>
            <th>Created</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {query.data?.items.map((match) => {
            const completed = match.status === "completed"
            return (
              <tr
                key={match.id}
                className={completed ? "rowlink" : undefined}
                style={completed ? { cursor: "pointer" } : undefined}
                onClick={completed ? () => router.push(`/dashboard/matches/${match.id}`) : undefined}
              >
                <td style={{ fontWeight: 600 }}>{match.jobTitle ?? "—"}</td>
                <td>{match.employer ?? "—"}</td>
                <td>
                  <ScoreBar score={match.score} size="sm" width={80} />
                </td>
                <td>
                  <Tag variant={completed ? "neutral" : match.status === "failed" ? "outline" : "accent"}>
                    {STATUS_LABEL[match.status] ?? match.status}
                  </Tag>
                </td>
                <td style={{ color: "var(--color-neutral-700)" }}>
                  {new Date(match.createdAt).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
                </td>
                <td style={{ textAlign: "right" }}>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    aria-label={`Delete report for ${match.jobTitle ?? "this match"}`}
                    disabled={deletingId === match.id}
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDelete(match.id, match.jobTitle ?? "this match")
                    }}
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
