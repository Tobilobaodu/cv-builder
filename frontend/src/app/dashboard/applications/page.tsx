"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Trash2 } from "lucide-react"

import {
  addApplicationNote,
  createApplication,
  deleteApplication,
  getApplicationStats,
  listApplications,
  updateApplicationStatus,
  type ApplicationStatus,
} from "@/lib/applications-api"
import { errorMessage } from "@/lib/api"
import { StatBand } from "@/components/modernist/stat-band"
import { Tag } from "@/components/modernist/tag"
import { TableShell } from "@/components/modernist/table-shell"

const STATUS_LABEL: Record<ApplicationStatus, string> = {
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  accepted: "Accepted",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  ghosted: "Ghosted",
}

const STATUS_VARIANT: Record<ApplicationStatus, "accent" | "accent-2" | "neutral" | "outline"> = {
  applied: "accent",
  interviewing: "accent-2",
  offer: "accent-2",
  accepted: "neutral",
  rejected: "outline",
  withdrawn: "outline",
  ghosted: "outline",
}

const ALL_STATUSES = Object.keys(STATUS_LABEL) as ApplicationStatus[]

export default function ApplicationsPage() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ["applications"], queryFn: () => listApplications({ limit: 100 }) })
  const statsQuery = useQuery({ queryKey: ["applications-stats"], queryFn: getApplicationStats })

  const [showForm, setShowForm] = useState(false)
  const [jobTitle, setJobTitle] = useState("")
  const [employer, setEmployer] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [updatingId, setUpdatingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [notePromptId, setNotePromptId] = useState<string | null>(null)
  const [noteDraft, setNoteDraft] = useState("")

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["applications"] })
    void queryClient.invalidateQueries({ queryKey: ["applications-stats"] })
  }

  async function handleCreate() {
    if (!jobTitle.trim() || !employer.trim()) {
      toast.error("Job title and employer are both required.")
      return
    }
    setIsSubmitting(true)
    try {
      await createApplication({ jobTitle: jobTitle.trim(), employer: employer.trim() })
      toast.success("Application logged.")
      setJobTitle("")
      setEmployer("")
      setShowForm(false)
      invalidate()
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't log that application."))
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleStatusChange(applicationId: string, status: ApplicationStatus) {
    setUpdatingId(applicationId)
    try {
      await updateApplicationStatus(applicationId, status)
      invalidate()
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't update that status."))
    } finally {
      setUpdatingId(null)
    }
  }

  async function handleAddNote(applicationId: string) {
    if (!noteDraft.trim()) {
      setNotePromptId(null)
      return
    }
    try {
      await addApplicationNote(applicationId, noteDraft.trim())
      toast.success("Note added.")
      setNotePromptId(null)
      setNoteDraft("")
      invalidate()
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't add that note."))
    }
  }

  async function handleDelete(applicationId: string, label: string) {
    if (!window.confirm(`Remove ${label} from your applications?`)) return
    setDeletingId(applicationId)
    try {
      await deleteApplication(applicationId)
      invalidate()
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't remove that application."))
    } finally {
      setDeletingId(null)
    }
  }

  const items = query.data?.items ?? []
  const stats = statsQuery.data

  return (
    <div style={{ padding: 48, display: "flex", flexDirection: "column", gap: 32, maxWidth: 1100 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 32 }}>
        <div>
          <h1 style={{ fontSize: 42, margin: "0 0 8px" }}>APPLICATIONS</h1>
          <p style={{ margin: 0, fontSize: 15, color: "var(--color-neutral-700)" }}>
            Track every application through to a response — response rate is the whole point.
          </p>
        </div>
        <button type="button" className="btn btn-primary" style={{ flex: "none" }} onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "Log an application"}
        </button>
      </div>

      {stats && stats.total > 0 && (
        <StatBand
          stats={[
            { label: "Total applications", value: stats.total },
            {
              label: "Response rate",
              value: stats.responseRate !== null ? `${Math.round(stats.responseRate * 100)}%` : "—",
              note: "Heard back, of any kind",
            },
            { label: "Interviewing", value: stats.byStatus.interviewing ?? 0 },
            { label: "Offers", value: (stats.byStatus.offer ?? 0) + (stats.byStatus.accepted ?? 0) },
          ]}
        />
      )}

      {showForm && (
        <section style={{ background: "var(--color-surface)", padding: 24, display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", gap: 12 }}>
            <div className="field" style={{ flex: 1 }}>
              <label>Job title</label>
              <input className="input" value={jobTitle} onChange={(e) => setJobTitle(e.target.value)} placeholder="Backend Engineer" />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>Employer</label>
              <input className="input" value={employer} onChange={(e) => setEmployer(e.target.value)} placeholder="Acme Co" />
            </div>
          </div>
          <div style={{ display: "flex", gap: 12 }}>
            <button type="button" className="btn btn-primary" disabled={isSubmitting} onClick={handleCreate}>
              {isSubmitting ? "Logging…" : "Log application"}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setShowForm(false)}>
              Cancel
            </button>
          </div>
        </section>
      )}

      <TableShell
        isLoading={query.isLoading}
        isError={query.isError}
        isEmpty={!!query.data && query.data.items.length === 0}
        emptyMessage="No applications logged yet — log one to start tracking your response rate."
      >
        <thead>
          <tr>
            <th>Role</th>
            <th>Employer</th>
            <th style={{ width: 170 }}>Status</th>
            <th>Applied</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((app) => (
            <tr key={app.id}>
              <td style={{ fontWeight: 600 }}>{app.jobTitle}</td>
              <td>{app.employer}</td>
              <td>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Tag variant={STATUS_VARIANT[app.status]}>{STATUS_LABEL[app.status]}</Tag>
                  <select
                    className="input"
                    style={{ padding: "4px 6px", fontSize: 12, width: "auto" }}
                    value=""
                    disabled={updatingId === app.id}
                    onChange={(e) => {
                      const next = e.target.value as ApplicationStatus
                      if (next) void handleStatusChange(app.id, next)
                    }}
                  >
                    <option value="">Change…</option>
                    {ALL_STATUSES.filter((s) => s !== app.status).map((s) => (
                      <option key={s} value={s}>
                        {STATUS_LABEL[s]}
                      </option>
                    ))}
                  </select>
                </div>
              </td>
              <td style={{ color: "var(--color-neutral-700)" }}>
                {new Date(app.appliedAt).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
              </td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                <button type="button" className="btn btn-ghost" onClick={() => setNotePromptId(app.id)}>
                  Note
                </button>
                <button
                  type="button"
                  className="btn btn-ghost"
                  aria-label={`Remove ${app.jobTitle}`}
                  disabled={deletingId === app.id}
                  onClick={() => handleDelete(app.id, app.jobTitle)}
                >
                  <Trash2 width={16} height={16} strokeWidth={2} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </TableShell>

      {notePromptId && (
        <section style={{ background: "var(--color-surface)", padding: 24 }}>
          <div className="field" style={{ marginBottom: 12 }}>
            <label>Note</label>
            <textarea
              className="input"
              style={{ minHeight: 80 }}
              placeholder="Recruiter called, no decision yet…"
              value={noteDraft}
              onChange={(e) => setNoteDraft(e.target.value)}
            />
          </div>
          <div style={{ display: "flex", gap: 12 }}>
            <button type="button" className="btn btn-primary" onClick={() => handleAddNote(notePromptId)}>
              Save note
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                setNotePromptId(null)
                setNoteDraft("")
              }}
            >
              Cancel
            </button>
          </div>
        </section>
      )}
    </div>
  )
}
