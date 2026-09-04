"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Check, Upload } from "lucide-react"

import { errorMessage, recordJourney } from "@/lib/api"
import {
  createMatch,
  getParsedCvProfile,
  submitJobPostText,
  submitJobPostUrl,
  uploadCv,
} from "@/lib/trial-api"
import { useJobPoll } from "@/hooks/use-job-poll"
import { usePollUntilReady } from "@/hooks/use-poll-until-ready"
import { SegmentedControl } from "@/components/modernist/segmented-control"
import { ProgressBar } from "@/components/modernist/progress-bar"
import { LinkedInImportHint } from "@/components/linkedin-import-hint"

// Mirrors backend app/api/v1/job_posts.py's JobPostTextRequest min_length.
const MIN_JOB_TEXT_CHARS = 100

export default function NewMatchPage() {
  const router = useRouter()
  const queryClient = useQueryClient()

  // ── CV: uploads immediately on file selection, like /try/upload ──
  const [cvFile, setCvFile] = useState<File | null>(null)
  const [cvId, setCvId] = useState<string | null>(null)
  const [cvUploadJobId, setCvUploadJobId] = useState<string | null>(null)
  const [cvError, setCvError] = useState<string | null>(null)
  const [isUploadingCv, setIsUploadingCv] = useState(false)
  const cvUploadPoll = useJobPoll(cvUploadJobId)

  const cvProfilePoll = usePollUntilReady(
    ["cv-profile-for-match", cvId],
    () => getParsedCvProfile(cvId as string),
    !!cvId && cvUploadPoll.isCompleted
  )

  // ── Job: only submitted once "Run my match" is clicked ──
  const [jobSource, setJobSource] = useState<"text" | "url">("text")
  const [jobText, setJobText] = useState("")
  const [jobUrl, setJobUrl] = useState("")
  const [jobPostId, setJobPostId] = useState<string | null>(null)
  const [jobSubmitJobId, setJobSubmitJobId] = useState<string | null>(null)
  const jobSubmitPoll = useJobPoll(jobSubmitJobId)

  // ── Match ──
  const [matchId, setMatchId] = useState<string | null>(null)
  const [matchJobId, setMatchJobId] = useState<string | null>(null)
  const matchPoll = useJobPoll(matchJobId)

  const [isRunning, setIsRunning] = useState(false)
  const matchStartedRef = useRef(false)

  async function handleCvFile(file: File) {
    setCvFile(file)
    setCvError(null)
    setCvId(null)
    setCvUploadJobId(null)
    setIsUploadingCv(true)
    try {
      const result = await uploadCv(file)
      setCvId(result.cvId)
      setCvUploadJobId(result.processingJobId)
    } catch (error) {
      setCvError(errorMessage(error, "Couldn't upload that file."))
    } finally {
      setIsUploadingCv(false)
    }
  }

  function onFileInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ""
    if (file) void handleCvFile(file)
  }

  function onDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault()
    const file = event.dataTransfer.files?.[0]
    if (file) void handleCvFile(file)
  }

  // Once the job post finishes processing and the CV's profile version is
  // known, create the match — the one step that needs both prerequisites.
  useEffect(() => {
    if (!isRunning || matchStartedRef.current) return
    if (!jobSubmitPoll.isCompleted || !jobPostId) return
    if (!cvProfilePoll.data?.profileVersionId) return
    matchStartedRef.current = true
    createMatch(cvProfilePoll.data.profileVersionId, jobPostId)
      .then((accepted) => {
        setMatchId(accepted.matchId)
        setMatchJobId(accepted.processingJobId)
      })
      .catch((error) => {
        matchStartedRef.current = false
        toast.error(errorMessage(error, "Couldn't start the match."))
        setIsRunning(false)
      })
  }, [isRunning, jobSubmitPoll.isCompleted, jobPostId, cvProfilePoll.data])

  useEffect(() => {
    if (cvProfilePoll.isTimedOut && isRunning) {
      toast.error("Your CV is taking longer than usual to process. Please try again shortly.")
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setIsRunning(false)
    }
  }, [cvProfilePoll.isTimedOut, isRunning])

  useEffect(() => {
    if (jobSubmitPoll.isFailed && isRunning) {
      toast.error("We couldn't process that job posting. Try pasting the description instead.")
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setIsRunning(false)
    }
  }, [jobSubmitPoll.isFailed, isRunning])

  useEffect(() => {
    if (matchPoll.isFailed && isRunning) {
      toast.error("The match failed to complete. Please try again.")
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setIsRunning(false)
    }
  }, [matchPoll.isFailed, isRunning])

  useEffect(() => {
    if (matchPoll.isCompleted && matchId && isRunning) {
      void queryClient.invalidateQueries({ queryKey: ["dashboard-matches"] })
      void queryClient.invalidateQueries({ queryKey: ["dashboard-cvs"] })
      router.push(`/dashboard/matches/${matchId}`)
    }
  }, [matchPoll.isCompleted, matchId, isRunning, router, queryClient])

  async function handleRunMatch() {
    if (!cvId) {
      toast.error("Upload a CV first.")
      return
    }
    if (!cvUploadPoll.isCompleted) {
      toast.error("Your CV is still processing — hang on a moment.")
      return
    }
    const text = jobText.trim()
    const url = jobUrl.trim()
    if (jobSource === "text" && text.length < MIN_JOB_TEXT_CHARS) {
      toast.error(`Paste at least ${MIN_JOB_TEXT_CHARS} characters of the job description.`)
      return
    }
    if (jobSource === "url" && !url) {
      toast.error("Enter a job posting URL.")
      return
    }

    setIsRunning(true)
    matchStartedRef.current = false
    try {
      const accepted =
        jobSource === "url" ? await submitJobPostUrl(url) : await submitJobPostText(text)
      setJobPostId(accepted.jobPostId)
      setJobSubmitJobId(accepted.processingJobId)
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't submit that job posting."))
      setIsRunning(false)
    }
  }

  const runLabel = !isRunning
    ? "Run my match"
    : !jobSubmitPoll.isCompleted
      ? "Reading the job posting…"
      : !cvProfilePoll.data
        ? "Reading your CV…"
        : !matchPoll.isCompleted
          ? "Scoring your match…"
          : "Almost there…"

  return (
    <div style={{ padding: 48, display: "flex", flexDirection: "column", gap: 40, maxWidth: 1000 }}>
      <div>
        <h1 style={{ fontSize: 42, margin: "0 0 8px" }}>NEW MATCH</h1>
        <p style={{ margin: 0, fontSize: 15, color: "var(--color-neutral-700)", maxWidth: "56ch" }}>
          One CV, one posting. You&apos;ll get a score, the evidence behind it, and a tailored file in about a
          minute.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 2, background: "var(--color-divider)" }}>
        {/* step 1 */}
        <div style={{ background: "var(--color-bg)", padding: "0 32px 0 0" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 20 }}>
            <span style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 13, color: "var(--color-accent)" }}>01</span>
            <h3 style={{ fontSize: 20, margin: 0 }}>YOUR CV</h3>
          </div>

          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
            style={{
              border: "2px dashed var(--color-divider)",
              padding: "40px 28px",
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              gap: 16,
              background: "var(--color-surface)",
            }}
          >
            <Upload width={26} height={26} strokeWidth={2} strokeLinecap="square" style={{ color: "var(--color-accent)" }} />
            <div style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 17 }}>Drag a file here</div>
            <div style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>PDF or DOCX, up to 20MB.</div>
            <label className="btn btn-secondary" style={{ cursor: "pointer" }}>
              Choose a file
              <input type="file" accept=".pdf,.docx" onChange={onFileInputChange} style={{ display: "none" }} />
            </label>
          </div>

          {!cvFile && (
            <LinkedInImportHint
              onOpen={() => recordJourney("cv_import_linkedin_hint_opened", 0)}
            />
          )}

          {cvFile && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 16,
                padding: "14px 0",
                borderBottom: "1px solid var(--color-divider)",
                marginTop: 16,
              }}
            >
              <div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{cvFile.name}</div>
                <div style={{ fontSize: 12, color: "var(--color-neutral-700)" }}>
                  {(cvFile.size / 1024).toFixed(0)} KB ·{" "}
                  {isUploadingCv
                    ? "uploading…"
                    : cvError
                      ? "failed"
                      : !cvUploadPoll.isCompleted
                        ? "processing…"
                        : "uploaded"}
                </div>
                <ProgressBar
                  isActive={!cvError && !cvUploadPoll.isCompleted}
                  expectedDurationMs={8000}
                  width={160}
                  height={6}
                />
              </div>
              {cvUploadPoll.isCompleted && !cvError && (
                <Check width={18} height={18} strokeWidth={2.4} strokeLinecap="square" style={{ color: "var(--color-text)" }} />
              )}
            </div>
          )}
          {cvError && <p style={{ fontSize: 13, color: "var(--color-accent-700)", marginTop: 8 }}>{cvError}</p>}
        </div>

        {/* step 2 */}
        <div style={{ background: "var(--color-bg)", padding: "0 0 0 32px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 20 }}>
            <span style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 13, color: "var(--color-accent)" }}>02</span>
            <h3 style={{ fontSize: 20, margin: 0 }}>THE JOB</h3>
          </div>

          <SegmentedControl
            name="jobinput"
            value={jobSource}
            onChange={setJobSource}
            options={[
              { value: "text", label: "Paste description" },
              { value: "url", label: "Job URL" },
            ]}
            style={{ marginBottom: 20 }}
          />

          {jobSource === "text" ? (
            <div className="field">
              <label>Job description</label>
              <textarea
                className="input"
                style={{ minHeight: 232 }}
                placeholder="Paste the posting text here…"
                value={jobText}
                onChange={(e) => setJobText(e.target.value)}
              />
            </div>
          ) : (
            <div className="field">
              <label>Job posting URL</label>
              <input
                className="input"
                type="url"
                placeholder="https://example.com/careers/role"
                value={jobUrl}
                onChange={(e) => setJobUrl(e.target.value)}
              />
            </div>
          )}
          <div style={{ fontSize: 12, color: "var(--color-neutral-700)", marginTop: 10 }}>
            Some sites block us from reading the page. Pasting always works.
          </div>
        </div>
      </div>

      <div style={{ borderTop: "1px solid var(--color-divider)", paddingTop: 24, display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <button type="button" className="btn btn-primary" disabled={isRunning || isUploadingCv} onClick={handleRunMatch}>
            {runLabel}
          </button>
          <div style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>This uses one of your remaining rewrites.</div>
        </div>
        <ProgressBar isActive={isRunning} expectedDurationMs={50000} width={280} />
      </div>
    </div>
  )
}
