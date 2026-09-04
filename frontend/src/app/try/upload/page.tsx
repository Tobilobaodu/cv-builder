"use client"

import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { ApiError, errorMessage } from "@/lib/api"
import {
  createMatchAnalysis,
  createTrialSession,
  downloadResumePdf,
  getCvRawText,
  getJobPost,
  recordJourney,
  streamResumeRewrite,
  submitJobPostUrl,
  uploadCv,
  type MatchAnalysisResult,
} from "@/lib/trial-api"
import { useAuthStore } from "@/store/auth-store"
import { useTrialStore } from "@/store/trial-store"
import { ScoreBar } from "@/components/modernist/score-bar"
import { Tag } from "@/components/modernist/tag"
import { SegmentedControl } from "@/components/modernist/segmented-control"
import { LinkedInImportHint } from "@/components/linkedin-import-hint"

type UploadState =
  | { phase: "idle" }
  | { phase: "uploading"; fileName: string }
  | { phase: "extracting"; fileName: string; cvId: string }
  | { phase: "ready"; fileName: string; cvId: string; text: string }
  | { phase: "failed"; fileName: string; message: string }

/** Rate limits are per client IP and easy to exhaust while testing
 *  (5 trial sessions/hour, 10 uploads/hour, 20 rewrites/hour). A 429 is not
 *  a bad file or a broken rewrite, so it must not be reported as one.
 *  errorMessage() only reads ApiError.body.detail, hence the explicit
 *  branch rather than a rethrown Error. */
function failureMessage(
  error: unknown,
  rateLimited: string,
  fallback: string
): string {
  if (error instanceof ApiError && error.status === 429) return rateLimited
  return errorMessage(error, fallback)
}

/** The job post can arrive as pasted text or be fetched from a URL by the
 *  backend's SSRF-guarded fetcher. Either way the analysis only ever reads
 *  the text in the textarea, so a fetch ends by filling it in. */
type JobFetchState =
  | { phase: "idle" }
  | { phase: "fetched"; url: string }
  | { phase: "failed"; message: string }

/** Score/gaps/tips — small, fast, shown as soon as it lands (S1). */
type AnalysisState =
  | { phase: "idle" }
  | { phase: "analysing" }
  | { phase: "ready"; result: MatchAnalysisResult }
  | { phase: "failed"; message: string }

/** The tailored CV, streamed in below the analysis once it's on screen
 *  (S2) — a separate, later-arriving piece, not the same "busy" spinner
 *  that gates the button. */
type RewriteState =
  | { phase: "idle" }
  | { phase: "streaming"; markdown: string }
  | { phase: "done"; markdown: string; informationNeeded: string[] }
  | { phase: "failed"; message: string }

const EXTRACT_POLL_MS = 2000
const EXTRACT_TIMEOUT_MS = 120_000
// job_fetch writes raw_text at status "structuring", before
// job_post_parse runs, so this does not wait for "completed".
const JOB_FETCH_POLL_MS = 1500
const JOB_FETCH_TIMEOUT_MS = 60_000
// Mirrors the API's own min_length on jobDescription.
const MIN_JOB_TEXT_CHARS = 40

function SkillList({
  title, items, tone, testId,
}: {
  title: string
  items: string[]
  tone: "matched" | "transferable" | "missing" | "keyword"
  testId: string
}) {
  if (!items.length) return null
  const variant = {
    matched: "neutral",
    transferable: "accent-2",
    missing: "outline",
    keyword: "accent",
  }[tone] as "neutral" | "accent-2" | "outline" | "accent"
  return (
    <div data-testid={testId}>
      <h4
        style={{
          margin: "0 0 8px",
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "var(--color-neutral-600)",
        }}
      >
        {title} <span style={{ marginLeft: 4 }}>({items.length})</span>
      </h4>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {items.map((item) => (
          <Tag key={item} variant={variant}>
            {item}
          </Tag>
        ))}
      </div>
    </div>
  )
}

export default function TailorPage() {
  const trialSessionId = useTrialStore((s) => s.trialSessionId)
  const setTrialSession = useTrialStore((s) => s.setTrialSession)
  const isAuthenticated = useAuthStore((s) => !!s.accessToken)

  const [upload, setUpload] = useState<UploadState>({ phase: "idle" })
  const [jobDescription, setJobDescription] = useState("")
  const [jobSource, setJobSource] = useState<"text" | "url">("text")
  const [jobUrl, setJobUrl] = useState("")
  const [jobFetch, setJobFetch] = useState<JobFetchState>({ phase: "idle" })
  const [targetTitle, setTargetTitle] = useState("")
  const [analysis, setAnalysis] = useState<AnalysisState>({ phase: "idle" })
  const [rewrite, setRewrite] = useState<RewriteState>({ phase: "idle" })
  // null when idle. "fetching" only occurs on the URL tab, where one
  // click covers both steps. Gates only the button/score wait — the
  // tailored-CV stream runs after this clears (see onAnalyse).
  const [busy, setBusy] = useState<null | "fetching" | "analysing">(null)
  const [cvPanelOpen, setCvPanelOpen] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const pollRef = useRef<number | null>(null)
  const jobPollRef = useRef<number | null>(null)
  // O4: wall clock from "upload accepted" to "analysis rendered" — the
  // journey jbs-solution-sheet.md's 30s target is measured against.
  // Cleared to null once recorded, so a second analyse on the same
  // upload (a different job description) doesn't get timed against the
  // original upload moment.
  const journeyStartRef = useRef<number | null>(null)

  // A trial session is needed before the very first upload, since upload
  // now fires on file selection rather than on a submit the user reaches
  // after /try has already minted one.
  const sessionPromiseRef = useRef<Promise<void> | null>(null)
  useEffect(() => {
    if (isAuthenticated || trialSessionId) return
    void ensureIdentity().catch(() => {
      /* surfaced on the first upload attempt instead */
    })
    // ensureIdentity is a hoisted declaration and reads identity from the
    // store at call time, so it needs no dependency entry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, trialSessionId])

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
      if (jobPollRef.current) window.clearInterval(jobPollRef.current)
    }
  }, [])

  function pollForText(cvId: string, fileName: string) {
    const startedAt = Date.now()
    if (pollRef.current) window.clearInterval(pollRef.current)
    pollRef.current = window.setInterval(async () => {
      if (Date.now() - startedAt > EXTRACT_TIMEOUT_MS) {
        if (pollRef.current) window.clearInterval(pollRef.current)
        setUpload({
          phase: "failed", fileName,
          message: "Extraction is taking longer than expected. Try uploading again.",
        })
        return
      }
      try {
        const raw = await getCvRawText(cvId)
        if (raw.canonicalText?.trim()) {
          if (pollRef.current) window.clearInterval(pollRef.current)
          setUpload({ phase: "ready", fileName, cvId, text: raw.canonicalText })
          setCvPanelOpen(true)
        }
      } catch {
        // 404 until extraction writes cv_raw_text — keep polling.
      }
    }, EXTRACT_POLL_MS)
  }

  /** Uploading on file selection races the bootstrap effect: a user who picks
   *  a file within the first moment would otherwise hit "Missing
   *  authentication token or trial session" (seen in e2e before this
   *  existed). Both paths share one in-flight promise — creating a second
   *  session would spend two of the five a client gets per hour. */
  function ensureIdentity(): Promise<void> {
    if (isAuthenticated) return Promise.resolve()
    if (useTrialStore.getState().trialSessionId) return Promise.resolve()
    if (!sessionPromiseRef.current) {
      sessionPromiseRef.current = createTrialSession()
        .then((session) => {
          setTrialSession(session.trialSessionId, session.expiresAt)
        })
        .catch((error) => {
          sessionPromiseRef.current = null // let the next attempt retry
          throw error
        })
    }
    return sessionPromiseRef.current
  }

  async function onFileSelected(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ""
    if (!file) return

    setAnalysis({ phase: "idle" })
    setRewrite({ phase: "idle" })
    setUpload({ phase: "uploading", fileName: file.name })

    try {
      await ensureIdentity()
    } catch (error) {
      setUpload({
        phase: "failed", fileName: file.name,
        message: failureMessage(
          error,
          "This browser has used its free trial sessions for the hour. " +
            "Sign in to keep going, or try again later.",
          "We couldn't start a session for this upload."
        ),
      })
      return
    }

    journeyStartRef.current = performance.now()
    let uploaded
    try {
      uploaded = await uploadCv(file)
    } catch (error) {
      setUpload({
        phase: "failed", fileName: file.name,
        message: failureMessage(
          error,
          "Upload limit reached for the hour. Try again later.",
          "We couldn't upload that file."
        ),
      })
      return
    }

    setUpload({ phase: "extracting", fileName: file.name, cvId: uploaded.cvId })
    pollForText(uploaded.cvId, file.name)
  }

  function stopJobPoll() {
    if (jobPollRef.current) window.clearInterval(jobPollRef.current)
    jobPollRef.current = null
  }

  /** Submit the URL and poll until the fetched text is available. Resolves
   *  with the text; rejects with a message already fit to show the user.
   *  Promise-shaped rather than setState-shaped because "Tailor my CV" has
   *  to fetch and then analyse within one click. */
  function fetchJobPostText(url: string): Promise<string> {
    return new Promise<string>((resolve, reject) => {
      ensureIdentity()
        .then(() => submitJobPostUrl(url))
        .then((accepted) => {
          const startedAt = Date.now()
          stopJobPoll()
          jobPollRef.current = window.setInterval(async () => {
            if (Date.now() - startedAt > JOB_FETCH_TIMEOUT_MS) {
              stopJobPoll()
              reject(
                new Error(
                  "Fetching that URL is taking longer than expected. " +
                    "Paste the description instead."
                )
              )
              return
            }
            try {
              const post = await getJobPost(accepted.jobPostId)
              if (post.status === "failed") {
                stopJobPoll()
                // job_fetch writes a specific reason for both SSRF
                // rejections and transport failures, and both already tell
                // the user to paste instead — surface it as-is.
                reject(
                  new Error(
                    post.errorMessage ?? "We couldn't fetch that job posting."
                  )
                )
                return
              }
              if (post.rawText?.trim()) {
                stopJobPoll()
                resolve(post.rawText)
              }
            } catch {
              // 404 until the worker writes the row — keep polling.
            }
          }, JOB_FETCH_POLL_MS)
        })
        .catch((error) =>
          reject(
            new Error(
              failureMessage(
                error,
                "URL-fetch limit reached for the hour. Try again later.",
                "We couldn't fetch that URL. Paste the description instead."
              )
            )
          )
        )
    })
  }

  // ── S7: start the JD fetch on blur/idle rather than on the analyse
  // click, so it's already in flight by the time the user presses the
  // button — CV extraction and the JD fetch are independent, but used to
  // run strictly sequentially because the fetch only started on click.
  // Keyed by URL string: if the user edits the field after priming,
  // jobFetchUrlRef won't match jobUrl.trim() at click-time, and onAnalyse
  // falls back to a fresh fetch rather than resolving against stale text —
  // silently analysing the previous URL would be a wrong-answer bug, not
  // just a slow one.
  const jobFetchPromiseRef = useRef<Promise<string> | null>(null)
  const jobFetchUrlRef = useRef<string>("")

  function primeJobFetch(url: string) {
    const trimmed = url.trim()
    if (!trimmed || trimmed === jobFetchUrlRef.current) return
    jobFetchUrlRef.current = trimmed
    jobFetchPromiseRef.current = fetchJobPostText(trimmed).catch((error) => {
      if (jobFetchUrlRef.current === trimmed) jobFetchPromiseRef.current = null
      throw error
    })
  }

  async function onAnalyse() {
    if (upload.phase !== "ready") return
    setAnalysis({ phase: "idle" })
    setRewrite({ phase: "idle" })
    setJobFetch({ phase: "idle" })

    // On the URL tab this is the only button: fetch first, then analyse,
    // without making the user press anything in between.
    let jobText = jobDescription.trim()
    if (jobSource === "url") {
      const url = jobUrl.trim()
      if (!url) return
      setBusy("fetching")
      try {
        const primed = jobFetchUrlRef.current === url ? jobFetchPromiseRef.current : null
        jobText = (await (primed ?? fetchJobPostText(url))).trim()
      } catch (error) {
        setJobFetch({
          phase: "failed",
          message:
            error instanceof Error
              ? error.message
              : "We couldn't fetch that URL. Paste the description instead.",
        })
        setBusy(null)
        return
      }
      setJobDescription(jobText)
      setJobFetch({ phase: "fetched", url })
      // Show what will actually be analysed, and leave it editable.
      setJobSource("text")
    }

    if (jobText.length < MIN_JOB_TEXT_CHARS) {
      setJobFetch({
        phase: "failed",
        message:
          "That page didn't give us enough text to work with. " +
          "Paste the description instead.",
      })
      setBusy(null)
      return
    }

    setBusy("analysing")
    setAnalysis({ phase: "analysing" })
    let analysisResult: MatchAnalysisResult
    try {
      analysisResult = await createMatchAnalysis({
        cvId: upload.cvId,
        jobDescription: jobText,
        targetTitle: targetTitle.trim() || undefined,
      })
    } catch (error) {
      setAnalysis({
        phase: "failed",
        message: failureMessage(
          error,
          "Analysis limit reached for the hour. Try again later.",
          "The analysis could not be completed."
        ),
      })
      setBusy(null)
      return
    }
    setAnalysis({ phase: "ready", result: analysisResult })
    setBusy(null)
    setCvPanelOpen(false)
    if (journeyStartRef.current != null) {
      recordJourney("cv_upload_to_analysis", (performance.now() - journeyStartRef.current) / 1000)
      journeyStartRef.current = null
    }

    // The tailored CV streams in separately, below the analysis that's
    // already on screen — not gated by `busy`, so the button and the rest
    // of the page are usable again while it writes.
    setRewrite({ phase: "streaming", markdown: "" })
    try {
      for await (const event of streamResumeRewrite({
        cvId: upload.cvId,
        jobDescription: jobText,
        targetTitle: targetTitle.trim() || undefined,
        analysis: analysisResult,
      })) {
        if (event.type === "delta") {
          setRewrite((prev) =>
            prev.phase === "streaming"
              ? { phase: "streaming", markdown: prev.markdown + event.text }
              : prev
          )
        } else if (event.type === "corrected") {
          setRewrite({
            phase: "done", markdown: event.text, informationNeeded: event.informationNeeded,
          })
        } else if (event.type === "done") {
          setRewrite((prev) => ({
            phase: "done",
            markdown: prev.phase === "streaming" ? prev.markdown : "",
            informationNeeded: event.informationNeeded,
          }))
        } else if (event.type === "error") {
          setRewrite({ phase: "failed", message: event.detail })
        }
      }
    } catch (error) {
      setRewrite({
        phase: "failed",
        message: failureMessage(
          error,
          "Rewrite limit reached for the hour. Try again later.",
          "The rewrite could not be completed."
        ),
      })
    }
  }

  // On the URL tab the URL is the input, so a description is not required
  // up front — "Tailor my CV" fetches it.
  const hasJobInput =
    jobSource === "url"
      ? jobUrl.trim().length > 0
      : jobDescription.trim().length >= MIN_JOB_TEXT_CHARS
  async function onDownloadPdf() {
    if (rewrite.phase !== "done") return
    setIsExporting(true)
    try {
      const blob = await downloadResumePdf({
        tailoredResumeMarkdown: rewrite.markdown,
        fileName: targetTitle.trim()
          ? `Tailored CV - ${targetTitle.trim()}`
          : "Tailored CV",
      })
      // Object URL rather than a data: URI — a resume PDF is tens of KB
      // and this avoids base64-inflating it through the address bar.
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = "tailored-cv.pdf"
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (error) {
      toast.error(
        failureMessage(
          error,
          "Export limit reached for the hour. Try again later.",
          "We couldn't build the PDF. Please try again."
        )
      )
    } finally {
      setIsExporting(false)
    }
  }

  const canAnalyse = upload.phase === "ready" && hasJobInput && !busy

  return (
    <div style={{ maxWidth: 1280, margin: "0 auto", padding: "40px 24px" }}>
      <header style={{ marginBottom: 32 }}>
        <h1 style={{ fontSize: 32, margin: "0 0 8px" }}>TAILOR YOUR CV</h1>
        <p style={{ margin: 0, maxWidth: "60ch", fontSize: 15, color: "var(--color-neutral-700)" }}>
          Your CV starts extracting the moment you choose a file. Add the role you
          want, and we&apos;ll show what already lands and what doesn&apos;t.
        </p>
      </header>

      <div style={{ display: "grid", gap: 24, gridTemplateColumns: "minmax(320px, 0.85fr) minmax(0, 1.15fr)" }}>
        {/* ── Left column: source ─────────────────────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div className="card">
            <div className="card-title">1. YOUR CV</div>
            <div className="field">
              <label htmlFor="cv-file">CV (PDF or DOCX)</label>
              <input
                id="cv-file"
                type="file"
                accept=".pdf,.docx"
                className="input"
                data-testid="input-cv-file"
                onChange={onFileSelected}
              />
            </div>

            {upload.phase === "idle" && (
              <LinkedInImportHint
                onOpen={() => recordJourney("cv_import_linkedin_hint_opened", 0)}
              />
            )}

            {upload.phase === "uploading" && (
              <div data-testid="status-uploading" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <p style={{ margin: 0, fontSize: 13, color: "var(--color-neutral-700)" }}>
                  Uploading {upload.fileName}…
                </p>
                <ProgressBar value={35} />
              </div>
            )}

            {upload.phase === "extracting" && (
              <div data-testid="status-extracting" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <p style={{ margin: 0, fontSize: 13, color: "var(--color-neutral-700)" }}>
                  Extracting text from {upload.fileName}…
                </p>
                <ProgressBar value={70} />
              </div>
            )}

            {upload.phase === "ready" && (
              <div
                data-testid="status-ready"
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                  background: "var(--color-bg)",
                  border: "1px solid var(--color-divider)",
                  padding: 12,
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <p style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{upload.fileName}</p>
                  <p style={{ margin: 0, fontSize: 12, color: "var(--color-neutral-700)" }}>
                    {upload.text.length.toLocaleString()} characters extracted
                  </p>
                </div>
                <button
                  type="button"
                  className="btn btn-secondary"
                  data-testid="button-view-extracted"
                  onClick={() => setCvPanelOpen(true)}
                >
                  View extracted CV
                </button>
              </div>
            )}

            {upload.phase === "failed" && (
              <p data-testid="status-upload-failed" style={{ margin: 0, fontSize: 13, color: "var(--color-accent-700)" }}>
                {upload.message}
              </p>
            )}
          </div>

          <div className="card">
            <div className="card-title">2. THE ROLE YOU WANT</div>
            <div className="field">
              <label htmlFor="target-title">Target title (optional)</label>
              <input
                id="target-title"
                className="input"
                data-testid="input-target-title"
                placeholder="e.g. Senior Product Designer"
                value={targetTitle}
                onChange={(e) => setTargetTitle(e.target.value)}
              />
            </div>

            <SegmentedControl
              name="job-source"
              value={jobSource}
              onChange={setJobSource}
              options={[
                { value: "text", label: "Paste description", testId: "tab-job-text" },
                { value: "url", label: "From a URL", testId: "tab-job-url" },
              ]}
            />

            {jobSource === "text" ? (
              <div className="field" style={{ marginTop: 16 }}>
                <label htmlFor="job-text">Job description</label>
                <textarea
                  id="job-text"
                  rows={10}
                  className="input"
                  data-testid="input-job-description"
                  placeholder="Paste the job posting text here…"
                  value={jobDescription}
                  onChange={(e) => setJobDescription(e.target.value)}
                />
                {jobFetch.phase === "fetched" && (
                  <p data-testid="status-job-fetched" style={{ margin: "6px 0 0", fontSize: 12, color: "var(--color-neutral-700)" }}>
                    Fetched from {jobFetch.url}. This is the text the analysis
                    reads — trim anything the page brought along with it.
                  </p>
                )}
                <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--color-neutral-700)" }}>
                  {jobDescription.trim().length < 40
                    ? `${40 - jobDescription.trim().length} more characters needed`
                    : "Job description looks ready"}
                </p>
              </div>
            ) : (
              <div className="field" style={{ marginTop: 16 }}>
                <label htmlFor="job-url">Job posting URL</label>
                <input
                  id="job-url"
                  type="url"
                  className="input"
                  data-testid="input-job-url"
                  placeholder="https://example.com/careers/role"
                  value={jobUrl}
                  onChange={(e) => setJobUrl(e.target.value)}
                  onBlur={(e) => primeJobFetch(e.target.value)}
                />
                {jobFetch.phase === "failed" && (
                  <p data-testid="status-job-fetch-failed" style={{ margin: "6px 0 0", fontSize: 13, color: "var(--color-accent-700)" }}>
                    {jobFetch.message}
                  </p>
                )}
                <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--color-neutral-700)" }}>
                  Tailor my CV fetches the page, drops the text into the box
                  next door so you can see and edit exactly what gets
                  analysed, then runs the analysis. Sites that block
                  automated fetching, and private or internal addresses, are
                  refused — paste the description instead.
                </p>
              </div>
            )}
            <button
              type="button"
              className="btn btn-primary btn-block"
              style={{ justifyContent: "center", textAlign: "center" }}
              data-testid="button-analyse"
              disabled={!canAnalyse}
              onClick={onAnalyse}
            >
              {busy === "fetching"
                ? "Fetching job post…"
                : busy === "analysing"
                  ? "Analysing…"
                  : "Tailor my CV"}
            </button>
          </div>
        </div>

        {/* ── Right column: results ───────────────────────────────── */}
        <div>
          {busy && (
            <div className="card" data-testid="state-loading">
              <ProgressBar value={60} />
              <p style={{ margin: 0, textAlign: "center", fontSize: 13, color: "var(--color-neutral-700)" }}>
                {busy === "fetching"
                  ? "Fetching the job post…"
                  : "Reading the role against your experience…"}
              </p>
            </div>
          )}

          {!busy && analysis.phase === "idle" && (
            <div
              className="card"
              data-testid="state-empty"
              style={{ minHeight: 420, alignItems: "center", justifyContent: "center", textAlign: "center" }}
            >
              <h2 style={{ fontSize: 20, margin: 0 }}>Your tailored CV appears here</h2>
              <p style={{ margin: "8px 0 0", maxWidth: 360, fontSize: 13, color: "var(--color-neutral-700)" }}>
                Add your CV and the job description. We&apos;ll show the fit score,
                what matches, what&apos;s transferable, and what&apos;s missing.
              </p>
            </div>
          )}

          {!busy && analysis.phase === "failed" && (
            <div className="card" data-testid="state-analysis-failed">
              <p style={{ margin: 0, fontSize: 13, color: "var(--color-accent-700)" }}>
                {analysis.message}
              </p>
            </div>
          )}

          {!busy && analysis.phase === "ready" && (
            <div data-testid="state-complete" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
              <div className="card" style={{ flexDirection: "row", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
                <div style={{ width: 140 }} data-testid="metric-ats-score">
                  <ScoreBar score={analysis.result.stats.atsScore} />
                </div>
                <div style={{ minWidth: 0 }}>
                  <Tag variant="accent" data-testid="text-match-label" >
                    {analysis.result.stats.matchLabel}
                  </Tag>
                  <p style={{ margin: "10px 0 0", fontSize: 13, color: "var(--color-neutral-700)" }}>
                    {analysis.result.stats.matchedSkills.length} matched ·{" "}
                    {analysis.result.stats.transferableSkills.length} transferable ·{" "}
                    {analysis.result.stats.missingSkills.length} missing
                  </p>
                  {analysis.result.stats.literalCoverage.present.length +
                    analysis.result.stats.literalCoverage.absent.length >
                    0 && (
                    <p
                      data-testid="text-literal-coverage"
                      style={{ margin: "4px 0 0", fontSize: 12, color: "var(--color-neutral-600)" }}
                    >
                      {Math.round(analysis.result.stats.literalCoverage.coverage * 100)}% keyword match —
                      what a plain ATS keyword filter (no synonym handling) would see
                    </p>
                  )}
                  {analysis.result.stats.sameOccupation === false &&
                    analysis.result.stats.cvOccupation &&
                    analysis.result.stats.jobOccupation && (
                      <p
                        data-testid="text-occupation-gap"
                        style={{ margin: "8px 0 0", fontSize: 13, fontWeight: 600, color: "var(--color-accent-700)" }}
                      >
                        Career change: your CV evidences{" "}
                        {analysis.result.stats.cvOccupation}, this role is{" "}
                        {analysis.result.stats.jobOccupation}. The score is capped
                        for a different profession.
                      </p>
                    )}
                </div>
              </div>

              <div className="card">
                <div className="card-title">Matching criteria</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
                  <SkillList title="Matched" tone="matched" testId="list-matched"
                             items={analysis.result.stats.matchedSkills} />
                  <SkillList title="Transferable" tone="transferable" testId="list-transferable"
                             items={analysis.result.stats.transferableSkills} />
                  <SkillList title="Not evidenced" tone="missing" testId="list-missing"
                             items={analysis.result.stats.missingSkills} />
                  <SkillList title="Priority keywords" tone="keyword" testId="list-keywords"
                             items={analysis.result.stats.priorityKeywords} />
                </div>
              </div>

              {analysis.result.matchNotes.length > 0 && (
                <div className="card">
                  <div className="card-title">Evidence-based match notes</div>
                  <ul data-testid="list-match-notes" style={{ margin: 0, paddingLeft: 20, fontSize: 13, display: "flex", flexDirection: "column", gap: 8 }}>
                    {analysis.result.matchNotes.map((note) => <li key={note}>{note}</li>)}
                  </ul>
                </div>
              )}

              {(() => {
                const informationNeeded = [
                  ...analysis.result.informationNeeded,
                  ...(rewrite.phase === "done" ? rewrite.informationNeeded : []),
                ]
                return informationNeeded.length > 0 && (
                  <div className="card">
                    <div className="card-title">Information that would strengthen this</div>
                    <ul data-testid="list-information-needed" style={{ margin: 0, paddingLeft: 20, fontSize: 13, display: "flex", flexDirection: "column", gap: 8 }}>
                      {informationNeeded.map((q) => <li key={q}>{q}</li>)}
                    </ul>
                  </div>
                )
              })()}

              <div className="card">
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
                  <div className="card-title" style={{ margin: 0 }}>Tailored CV</div>
                  <button
                    type="button"
                    className="btn btn-primary"
                    data-testid="button-download-pdf"
                    disabled={isExporting || rewrite.phase !== "done"}
                    onClick={onDownloadPdf}
                  >
                    {isExporting ? "Building PDF…" : "Download PDF"}
                  </button>
                </div>
                {rewrite.phase === "failed" ? (
                  <p data-testid="status-rewrite-failed" style={{ margin: 0, fontSize: 13, color: "var(--color-accent-700)" }}>
                    {rewrite.message}
                  </p>
                ) : (
                  <pre
                    data-testid="text-tailored-cv"
                    style={{
                      maxHeight: 520,
                      overflow: "auto",
                      whiteSpace: "pre-wrap",
                      background: "var(--color-bg)",
                      border: "1px solid var(--color-divider)",
                      padding: 16,
                      fontSize: 13,
                      margin: 0,
                    }}
                  >
                    {rewrite.phase === "idle"
                      ? ""
                      : rewrite.phase === "streaming" || rewrite.phase === "done"
                        ? rewrite.markdown
                        : ""}
                    {rewrite.phase === "streaming" && (
                      <span data-testid="text-tailored-cv-cursor" aria-hidden style={{ opacity: 0.4 }}>▍</span>
                    )}
                  </pre>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Left slide-over: the extracted CV text ─────────────────── */}
      {cvPanelOpen && upload.phase === "ready" && (
        <div style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex" }} data-testid="modal-extracted-cv">
          <button
            type="button"
            aria-label="Close extracted CV"
            data-testid="button-close-extracted-backdrop"
            style={{ position: "absolute", inset: 0, background: "color-mix(in srgb, var(--color-neutral-900) 55%, transparent)", border: 0, cursor: "pointer" }}
            onClick={() => setCvPanelOpen(false)}
          />
          <aside
            style={{
              position: "relative",
              display: "flex",
              flexDirection: "column",
              height: "100%",
              width: "100%",
              maxWidth: 560,
              borderRight: "1px solid var(--color-divider)",
              background: "var(--color-bg)",
              boxShadow: "var(--shadow-lg)",
            }}
          >
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, borderBottom: "1px solid var(--color-divider)", padding: 16 }}>
              <div style={{ minWidth: 0 }}>
                <h2 style={{ fontSize: 20, margin: 0 }}>Extracted CV</h2>
                <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--color-neutral-700)" }}>
                  {upload.fileName} · {upload.text.length.toLocaleString()} characters
                </p>
              </div>
              <button
                type="button"
                className="btn btn-ghost"
                data-testid="button-close-extracted"
                onClick={() => setCvPanelOpen(false)}
              >
                Close
              </button>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
              <pre
                data-testid="text-extracted-cv"
                style={{ whiteSpace: "pre-wrap", fontFamily: "monospace", fontSize: 12, lineHeight: 1.6, margin: 0 }}
              >
                {upload.text}
              </pre>
            </div>
            <div style={{ borderTop: "1px solid var(--color-divider)", padding: 12 }}>
              <p style={{ margin: 0, fontSize: 12, color: "var(--color-neutral-700)" }}>
                This is exactly the text the analysis reads. If something is missing
                or garbled here, it will be missing from the tailored CV too.
              </p>
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div style={{ height: 6, background: "var(--color-neutral-300)" }}>
      <div style={{ height: "100%", width: `${value}%`, background: "var(--color-accent)" }} />
    </div>
  )
}
