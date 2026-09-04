"use client"

import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Trash2 } from "lucide-react"

import { deleteCoverLetterWorkflow, listCoverLetterWorkflows, type CoverLetterWorkflowListItem } from "@/lib/dashboard-api"
import {
  approveCoverLetterDraft,
  exportCoverLetter,
  getCoverLetterDraft,
  getCoverLetterQuestions,
  regenerateCoverLetter,
  submitCoverLetterAnswers,
  type CoverLetterQuestion,
} from "@/lib/trial-api"
import { errorMessage } from "@/lib/api"
import { Tag } from "@/components/modernist/tag"
import { TableShell } from "@/components/modernist/table-shell"
import { ProgressBar } from "@/components/modernist/progress-bar"
import { ExportButton } from "@/components/export-button"

const STATUS_LABEL: Record<string, string> = {
  awaiting_answers: "In progress",
  generating: "Generating",
  draft_ready: "Draft ready",
  approved: "Approved",
  generation_failed: "Failed",
}

type ActiveWorkflow = {
  id: string
  currentStep: number
  totalSteps: number
  status: string
  jobTitle: string | null
  employer: string | null
}

export default function CoverLettersPage() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ["dashboard-cover-letters"], queryFn: () => listCoverLetterWorkflows() })
  const [active, setActive] = useState<ActiveWorkflow | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  async function handleDelete(e: React.MouseEvent, wf: CoverLetterWorkflowListItem) {
    e.stopPropagation()
    if (!window.confirm(`Delete the cover letter for ${wf.jobTitle ?? "this role"}? This can't be undone.`)) return
    setDeletingId(wf.id)
    try {
      await deleteCoverLetterWorkflow(wf.id)
      if (active?.id === wf.id) setActive(null)
      void queryClient.invalidateQueries({ queryKey: ["dashboard-cover-letters"] })
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't delete that cover letter."))
    } finally {
      setDeletingId(null)
    }
  }

  // Default to the newest resumable workflow once the list loads, so the
  // guided card has something to show without requiring an extra click.
  useEffect(() => {
    if (active || !query.data) return
    const resumable = query.data.items.find((wf) => wf.status !== "approved")
    // Deliberate synchronous default once the list loads — same pattern as
    // use-require-auth.ts's hydration check, not a derived-state anti-pattern.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (resumable) setActive(fromListItem(resumable))
  }, [active, query.data])

  function resume(wf: CoverLetterWorkflowListItem) {
    setActive(fromListItem(wf))
  }

  return (
    <div style={{ padding: 48, display: "flex", flexDirection: "column", gap: 40, maxWidth: 1000 }}>
      <div>
        <h1 style={{ fontSize: 42, margin: "0 0 8px" }}>COVER LETTERS</h1>
        <p style={{ margin: 0, fontSize: 15, color: "var(--color-neutral-700)", maxWidth: "56ch" }}>
          Built from a few short questions and your own CV. Nothing is written that your experience doesn&apos;t
          already support.
        </p>
      </div>

      {active ? (
        <WorkflowCard
          workflow={active}
          onWorkflowChange={setActive}
          onFinished={() => void queryClient.invalidateQueries({ queryKey: ["dashboard-cover-letters"] })}
        />
      ) : (
        <section style={{ background: "var(--color-surface)", padding: 32 }}>
          <p style={{ margin: 0, fontSize: 14, color: "var(--color-neutral-700)" }}>
            No letter in progress. Start one from a match&apos;s report page (&ldquo;Write the cover letter&rdquo;), or
            continue an existing one below.
          </p>
        </section>
      )}

      <section>
        <h3 style={{ fontSize: 20, margin: "0 0 16px" }}>ALL LETTERS</h3>
        <TableShell
          isLoading={query.isLoading}
          isError={query.isError}
          isEmpty={!!query.data && query.data.items.length === 0}
          emptyMessage="No cover letters yet — start one from a match's report page."
        >
          <thead>
            <tr>
              <th>Role</th>
              <th>Employer</th>
              <th>Status</th>
              <th>Step</th>
              <th>Created</th>
              <th></th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {query.data?.items.map((wf) => (
              <tr key={wf.id} className="rowlink" style={{ cursor: wf.status !== "approved" ? "pointer" : undefined }} onClick={() => wf.status !== "approved" && resume(wf)}>
                <td style={{ fontWeight: 600 }}>{wf.jobTitle ?? "—"}</td>
                <td>{wf.employer ?? "—"}</td>
                <td>
                  <Tag variant={wf.status === "approved" ? "neutral" : "accent"}>{STATUS_LABEL[wf.status] ?? wf.status}</Tag>
                </td>
                <td>{wf.currentStep}</td>
                <td style={{ color: "var(--color-neutral-700)" }}>
                  {new Date(wf.createdAt).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
                </td>
                <td style={{ textAlign: "right" }}>
                  {wf.status === "approved" ? (
                    <ExportButton
                      label="Export .docx"
                      startExport={() => exportCoverLetter(wf.id)}
                      filename={`cover-letter-${wf.id}.docx`}
                    />
                  ) : (
                    <span style={{ fontSize: 13, color: "var(--color-neutral-600)" }}>—</span>
                  )}
                </td>
                <td style={{ textAlign: "right" }}>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    aria-label={`Delete cover letter for ${wf.jobTitle ?? "this role"}`}
                    disabled={deletingId === wf.id}
                    onClick={(e) => handleDelete(e, wf)}
                  >
                    <Trash2 width={16} height={16} strokeWidth={2} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </TableShell>
      </section>
    </div>
  )
}

function fromListItem(wf: CoverLetterWorkflowListItem): ActiveWorkflow {
  return {
    id: wf.id,
    currentStep: wf.currentStep,
    totalSteps: wf.totalSteps,
    status: wf.status,
    jobTitle: wf.jobTitle,
    employer: wf.employer,
  }
}

function WorkflowCard({
  workflow,
  onWorkflowChange,
  onFinished,
}: {
  workflow: ActiveWorkflow
  onWorkflowChange: (wf: ActiveWorkflow) => void
  onFinished: () => void
}) {
  if (workflow.status === "awaiting_answers") {
    return <QuestionStep workflow={workflow} onWorkflowChange={onWorkflowChange} onFinished={onFinished} />
  }
  return <DraftStep workflow={workflow} onFinished={onFinished} />
}

function QuestionStep({
  workflow,
  onWorkflowChange,
  onFinished,
}: {
  workflow: ActiveWorkflow
  onWorkflowChange: (wf: ActiveWorkflow) => void
  onFinished: () => void
}) {
  const questionsQuery = useQuery({
    queryKey: ["cover-letter-questions", workflow.id, workflow.currentStep],
    queryFn: () => getCoverLetterQuestions(workflow.id),
  })
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAnswers({})
  }, [workflow.currentStep])

  async function handleContinue() {
    const questions: CoverLetterQuestion[] = questionsQuery.data ?? []
    const payload = questions
      .filter((q) => (answers[q.id] ?? "").trim().length > 0)
      .map((q) => ({ questionId: q.id, answerText: answers[q.id].trim() }))
    if (payload.length === 0) {
      toast.error("Answer at least one question to continue.")
      return
    }
    setIsSubmitting(true)
    try {
      const updated = await submitCoverLetterAnswers(workflow.id, payload)
      onWorkflowChange({
        id: updated.id,
        currentStep: updated.currentStep,
        totalSteps: updated.totalSteps ?? workflow.totalSteps,
        status: updated.status,
        jobTitle: workflow.jobTitle,
        employer: workflow.employer,
      })
      if (updated.status !== "awaiting_answers") onFinished()
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't save your answers."))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <section style={{ background: "var(--color-surface)", padding: 32 }}>
      <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-accent)", marginBottom: 8 }}>
        In progress · step {workflow.currentStep} of {workflow.totalSteps}
      </div>
      <h3 style={{ fontSize: 25, margin: "0 0 6px" }}>{(workflow.jobTitle ?? "Untitled role").toUpperCase()}</h3>
      <p style={{ margin: "0 0 24px", fontSize: 14, color: "var(--color-neutral-700)" }}>{workflow.employer ?? ""}</p>

      <div style={{ display: "flex", gap: 4, marginBottom: 28 }}>
        {Array.from({ length: workflow.totalSteps }).map((_, i) => (
          <div
            key={i}
            style={{
              flex: 1,
              height: 6,
              background: i < workflow.currentStep ? "var(--color-accent)" : "var(--color-neutral-400)",
            }}
          />
        ))}
      </div>

      {questionsQuery.isLoading && <p style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>Loading questions…</p>}

      <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 640 }}>
        {questionsQuery.data?.map((q) => (
          <div className="field" key={q.id}>
            <label>{q.questionText}</label>
            <textarea
              className="input"
              placeholder="One or two sentences is enough — we'll shape it."
              value={answers[q.id] ?? ""}
              onChange={(e) => setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))}
            />
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 12, marginTop: 20 }}>
        <button type="button" className="btn btn-primary" disabled={isSubmitting} onClick={handleContinue}>
          {isSubmitting ? "Saving…" : "Continue"}
        </button>
      </div>
    </section>
  )
}

function DraftStep({ workflow, onFinished }: { workflow: ActiveWorkflow; onFinished: () => void }) {
  const [isBusy, setIsBusy] = useState(false)
  const draftQuery = useQuery({
    queryKey: ["cover-letter-draft", workflow.id],
    queryFn: () => getCoverLetterDraft(workflow.id),
    enabled: workflow.status === "generating" || workflow.status === "draft_ready" || workflow.status === "approved",
    retry: false,
    refetchInterval: (q) => (q.state.data?.status === "pending" || workflow.status === "generating" ? 3000 : false),
  })

  async function handleApprove() {
    setIsBusy(true)
    try {
      await approveCoverLetterDraft(workflow.id)
      toast.success("Cover letter approved.")
      onFinished()
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't approve this letter."))
    } finally {
      setIsBusy(false)
    }
  }

  async function handleRegenerate() {
    setIsBusy(true)
    try {
      await regenerateCoverLetter(workflow.id)
      toast.info("Regenerating your letter…")
      void draftQuery.refetch()
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't regenerate this letter."))
    } finally {
      setIsBusy(false)
    }
  }

  return (
    <section style={{ background: "var(--color-surface)", padding: 32 }}>
      <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-accent)", marginBottom: 8 }}>
        {workflow.status === "generating" ? "Generating" : "Draft ready"}
      </div>
      <h3 style={{ fontSize: 25, margin: "0 0 6px" }}>{(workflow.jobTitle ?? "Untitled role").toUpperCase()}</h3>
      <p style={{ margin: "0 0 24px", fontSize: 14, color: "var(--color-neutral-700)" }}>{workflow.employer ?? ""}</p>

      {workflow.status === "generating" || draftQuery.isLoading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <p style={{ margin: 0, fontSize: 14, color: "var(--color-neutral-700)" }}>Writing your letter — this takes about a minute.</p>
          <ProgressBar isActive width={280} expectedDurationMs={60000} />
        </div>
      ) : draftQuery.data ? (
        <>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              fontFamily: "var(--font-body)",
              fontSize: 14,
              lineHeight: 1.6,
              background: "var(--color-bg)",
              padding: 20,
              maxHeight: 420,
              overflow: "auto",
              margin: "0 0 20px",
            }}
          >
            {draftQuery.data.bodyText}
          </pre>
          <div style={{ display: "flex", gap: 12 }}>
            {workflow.status !== "approved" && (
              <button type="button" className="btn btn-primary" disabled={isBusy} onClick={handleApprove}>
                Approve
              </button>
            )}
            <button type="button" className="btn btn-secondary" disabled={isBusy} onClick={handleRegenerate}>
              Regenerate
            </button>
          </div>
        </>
      ) : (
        <p style={{ fontSize: 14, color: "var(--color-neutral-700)" }}>No draft yet.</p>
      )}
    </section>
  )
}
