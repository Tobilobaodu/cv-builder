"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { PaywallDialog } from "@/components/paywall-dialog"
import { useJobPoll } from "@/hooks/use-job-poll"
import { usePollUntilReady } from "@/hooks/use-poll-until-ready"
import { useSimulatedProgress } from "@/hooks/use-simulated-progress"
import { useTrialStore } from "@/store/trial-store"
import { useAuthStore } from "@/store/auth-store"
import { errorMessage } from "@/lib/api"
import {
  getParsedCvProfile,
  getJobPost,
  createMatch,
  getMatch,
  createTailoredCv,
  getTailoredCvDraft,
  approveTailoredCv,
  createCvExport,
  getExport,
  downloadExport,
} from "@/lib/trial-api"

export default function TrialResultsPage() {
  const router = useRouter()
  const {
    cvId,
    cvProcessingJobId,
    jobPostId,
    jobPostProcessingJobId,
    cvProfileVersionId,
    matchId,
    draftId,
    setWorkflow,
  } = useTrialStore()
  const isAuthenticated = useAuthStore((s) => !!s.accessToken)
  const [paywallOpen, setPaywallOpen] = useState(false)

  useEffect(() => {
    if (!cvId || !jobPostId) {
      router.replace("/try")
    }
  }, [cvId, jobPostId, router])

  // ── Stage 1: wait for CV parsing and job post parsing ──────────────
  const cvJob = useJobPoll(cvProcessingJobId)
  const jobPostJob = useJobPoll(jobPostProcessingJobId)

  const cvProfilePoll = usePollUntilReady(
    ["cv-profile", cvId],
    () => getParsedCvProfile(cvId as string),
    cvJob.isCompleted && !!cvId
  )

  const jobPostQuery = useQuery({
    queryKey: ["job-post", jobPostId],
    queryFn: () => getJobPost(jobPostId as string),
    enabled: jobPostJob.isCompleted && !!jobPostId,
  })

  useEffect(() => {
    if (cvProfilePoll.data && cvProfilePoll.data.profileVersionId !== cvProfileVersionId) {
      setWorkflow({ cvProfileVersionId: cvProfilePoll.data.profileVersionId })
    }
  }, [cvProfilePoll.data, cvProfileVersionId, setWorkflow])

  useEffect(() => {
    if (cvProfilePoll.isTimedOut) {
      toast.error("Your CV is taking longer than usual to process. Please try again shortly.")
    }
  }, [cvProfilePoll.isTimedOut])

  // ── Stage 2: create the match once both are parsed ─────────────────
  const [matchProcessingJobId, setMatchProcessingJobId] = useState<string | null>(null)
  const matchStartedRef = useRef(false)

  useEffect(() => {
    const profileId = cvProfilePoll.data?.profileVersionId
    // POST /matches 404s if the job post isn't structured yet (app/api/v1/
    // matches.py requires a JobPostProfile row to already exist) — gating on
    // jobPostId alone races CV parsing finishing before job post parsing
    // does, firing a doomed request that never gets retried. Wait for the
    // job post's own profile to be present, not just its id.
    if (!profileId || !jobPostQuery.data?.profile || matchId || matchStartedRef.current) return
    matchStartedRef.current = true

    createMatch(profileId, jobPostId as string)
      .then((result) => {
        setWorkflow({ matchId: result.matchId })
        setMatchProcessingJobId(result.processingJobId)
      })
      .catch((error) => {
        matchStartedRef.current = false
        toast.error(errorMessage(error, "Couldn't compare your CV against this job."))
      })
  }, [cvProfilePoll.data, jobPostQuery.data, jobPostId, matchId, setWorkflow])

  // matchId is set as soon as the match is *created* (queued) — the actual
  // scoring is async, tracked separately via its own processing job.
  const matchJob = useJobPoll(matchProcessingJobId)
  const matchQuery = useQuery({
    queryKey: ["match", matchId],
    queryFn: () => getMatch(matchId as string),
    enabled: !!matchId && matchJob.isCompleted,
    refetchInterval: (q) => (q.state.data?.status === "completed" ? false : 2000),
  })
  const matchProgress = useSimulatedProgress(!matchQuery.data, 8000)

  // ── Stage 3: generate the tailored CV once the match is complete ───
  const [cvGenerateJobId, setCvGenerateJobId] = useState<string | null>(null)
  const draftStartedRef = useRef(false)

  useEffect(() => {
    if (matchQuery.data?.status !== "completed" || !matchId || draftId || draftStartedRef.current) return
    draftStartedRef.current = true

    createTailoredCv(matchId)
      .then((result) => setCvGenerateJobId(result.jobId))
      .catch((error) => {
        draftStartedRef.current = false
        toast.error(errorMessage(error, "Couldn't generate your tailored CV."))
      })
  }, [matchQuery.data?.status, matchId, draftId])

  const draftJob = useJobPoll(cvGenerateJobId)

  useEffect(() => {
    if (draftJob.isCompleted && draftJob.job?.sourceEntityId && draftJob.job.sourceEntityId !== draftId) {
      setWorkflow({ draftId: draftJob.job.sourceEntityId })
    }
  }, [draftJob.isCompleted, draftJob.job, draftId, setWorkflow])

  const draftQuery = useQuery({
    queryKey: ["tailored-cv-draft", draftId],
    queryFn: () => getTailoredCvDraft(draftId as string),
    enabled: !!draftId,
  })
  const draftProgress = useSimulatedProgress(!!matchQuery.data && !draftQuery.data, 20000)

  // Exporting requires status 'approved' (app/api/v1/exports.py 409s
  // otherwise). The trial flow has no manual review step, so approve
  // automatically the moment generation succeeds.
  const approveStartedRef = useRef(false)
  useEffect(() => {
    if (draftQuery.data?.status !== "generated" || !draftId || approveStartedRef.current) return
    approveStartedRef.current = true

    approveTailoredCv(draftId)
      .then(() => draftQuery.refetch())
      .catch((error) => {
        approveStartedRef.current = false
        toast.error(errorMessage(error, "Couldn't finalize your tailored CV."))
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftQuery.data?.status, draftId])

  // ── Download (Sprint 5's trial-accessible export) ───────────────────
  const [exportId, setExportId] = useState<string | null>(null)
  const [isStartingExport, setIsStartingExport] = useState(false)
  const exportQuery = useQuery({
    queryKey: ["export", exportId],
    queryFn: () => getExport(exportId as string),
    enabled: !!exportId,
    refetchInterval: (q) => (q.state.data?.status === "completed" ? false : 1500),
  })
  const [hasDownloaded, setHasDownloaded] = useState(false)
  const exportProgress = useSimulatedProgress(isStartingExport || !!exportId, 8000)

  useEffect(() => {
    if (exportQuery.data?.status === "completed" && exportId && !hasDownloaded) {
      // Synchronous guard against a duplicate download if this effect
      // re-runs before downloadExport's promise settles — not derived
      // state, an intentional one-shot trigger latch.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setHasDownloaded(true)
      downloadExport(exportId, "tailored-cv.docx").catch((error) => {
        toast.error(errorMessage(error, "Download failed. Please try again."))
      })
    }
  }, [exportQuery.data?.status, exportId, hasDownloaded])

  async function handleDownload() {
    if (!draftId) return
    setIsStartingExport(true)
    try {
      const result = await createCvExport(draftId)
      setExportId(result.id)
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't start your download."))
    } finally {
      setIsStartingExport(false)
    }
  }

  if (!cvId || !jobPostId) {
    return null
  }

  const cvParseFailed = cvJob.isFailed
  const jobPostParseFailed = jobPostJob.isFailed

  if (cvParseFailed || jobPostParseFailed) {
    return (
      <div className="mx-auto max-w-xl px-4 py-24 text-center">
        <p className="text-destructive">
          {cvParseFailed
            ? "We couldn't read your CV. Please try a different file."
            : "We couldn't read that job posting. Please try again."}
        </p>
        <Button className="mt-4" onClick={() => router.push("/try/upload")}>
          Try again
        </Button>
      </div>
    )
  }

  const match = matchQuery.data
  const draft = draftQuery.data

  return (
    <div className="mx-auto max-w-2xl px-4 py-16">
      <h1 className="text-2xl font-semibold">Your match results</h1>

      {!match && (
        <Card className="mt-6">
          <CardContent className="py-8 text-center text-muted-foreground">
            <p>Analyzing your CV against this job…</p>
            <Progress value={matchProgress} className="mt-4" />
          </CardContent>
        </Card>
      )}

      {match && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Match score: {match.score ?? "—"}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {match.summaryAnalysis && <p>{match.summaryAnalysis}</p>}
            <dl className="mt-2 grid grid-cols-3 gap-4 text-center text-sm">
              <div>
                <dt className="text-muted-foreground">Supported</dt>
                <dd className="text-lg font-semibold">{match.supportedCount ?? 0}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Partial</dt>
                <dd className="text-lg font-semibold">{match.partialCount ?? 0}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Unsupported</dt>
                <dd className="text-lg font-semibold">{match.unsupportedCount ?? 0}</dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      )}

      {match && !draft && (
        <Card className="mt-6">
          <CardContent className="py-8 text-center text-muted-foreground">
            Generating your tailored CV…
            <Progress value={draftProgress} className="mt-4" />
          </CardContent>
        </Card>
      )}

      {draft && draft.status === "failed" && (
        <Card className="mt-6">
          <CardContent className="py-8 text-center text-muted-foreground">
            <p>
              We couldn&apos;t find enough matching, verifiable experience in your CV to
              generate tailored content for this job — we don&apos;t fabricate content that
              isn&apos;t backed by your actual CV.
            </p>
          </CardContent>
        </Card>
      )}

      {draft && draft.status !== "failed" && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Your tailored CV</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-3">
              {draft.sections
                .sort((a, b) => a.orderIndex - b.orderIndex)
                .map((section) => (
                  <div key={section.id}>
                    <p className="text-sm font-medium uppercase text-muted-foreground">
                      {section.sectionType}
                    </p>
                    <p className="whitespace-pre-wrap text-sm">{section.contentText}</p>
                  </div>
                ))}
            </div>
            <Button
              onClick={handleDownload}
              disabled={draft.status !== "approved" || isStartingExport || hasDownloaded}
            >
              {hasDownloaded
                ? "Downloaded"
                : draft.status !== "approved"
                  ? "Finalizing…"
                  : isStartingExport || exportId
                    ? "Preparing your download…"
                    : "Download trial CV"}
            </Button>
            <Progress value={exportProgress} className="mt-3 max-w-xs" />
            {isAuthenticated ? (
              <Button variant="outline" disabled title="Coming soon">
                Create a cover letter for this job
              </Button>
            ) : (
              <Button variant="outline" onClick={() => setPaywallOpen(true)}>
                Create a cover letter for this job
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      <PaywallDialog open={paywallOpen} onOpenChange={setPaywallOpen} />
    </div>
  )
}
