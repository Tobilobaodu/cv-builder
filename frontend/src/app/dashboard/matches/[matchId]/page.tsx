"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { listCvs, listMatches } from "@/lib/dashboard-api"
import {
  approveTailoredCv,
  createCvExport,
  createTailoredCv,
  downloadExport,
  getExport,
  getMatch,
  startCoverLetterWorkflow,
} from "@/lib/trial-api"
import { errorMessage } from "@/lib/api"
import { useCvAnalysis } from "@/hooks/use-cv-analysis"
import { useJobPoll } from "@/hooks/use-job-poll"
import { ScoreBar } from "@/components/modernist/score-bar"
import { ProgressBar } from "@/components/modernist/progress-bar"
import { EvidenceBand } from "@/components/modernist/evidence-band"
import { CollapsibleIssueSection } from "@/components/modernist/collapsible-issue-section"
import { Tag } from "@/components/modernist/tag"

export default function ReportDetailPage() {
  const params = useParams<{ matchId: string }>()
  const matchId = params.matchId
  const router = useRouter()

  const matchQuery = useQuery({
    queryKey: ["match", matchId],
    queryFn: () => getMatch(matchId),
    enabled: !!matchId,
  })

  // GET /matches/{id} now returns jobPostId/cvId/jobTitle/employer
  // directly; the matches-list cache is kept only as a fallback (e.g. an
  // older match_run row with no match_json yet) so this page degrades
  // gracefully instead of guessing.
  const matchListQuery = useQuery({ queryKey: ["dashboard-matches"], queryFn: () => listMatches() })
  const matchListItem = matchListQuery.data?.items.find((m) => m.id === matchId)

  const cvsQuery = useQuery({ queryKey: ["dashboard-cvs"], queryFn: () => listCvs() })
  const latestCv = cvsQuery.data?.items[0]
  const { analysis, isScoring } = useCvAnalysis(latestCv?.id ?? null)

  const [isDownloading, setIsDownloading] = useState(false)
  const [tailoredJobId, setTailoredJobId] = useState<string | null>(null)
  const tailoredPoll = useJobPoll(tailoredJobId)
  const [isStartingLetter, setIsStartingLetter] = useState(false)
  const handledDraftRef = useRef<string | null>(null)

  const match = matchQuery.data
  const jobTitleForDownload = match?.jobTitle ?? "match"

  async function handleDownloadTailoredCv() {
    setIsDownloading(true)
    try {
      const job = await createTailoredCv(matchId)
      setTailoredJobId(job.jobId)
      // The rest of the chain (approve -> export -> download) runs from the
      // poll effect below once the draft job completes; give the user
      // feedback right away.
      toast.info("Building your tailored CV…")
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't start building the tailored CV."))
      setIsDownloading(false)
    }
  }

  async function finishTailoredCvDownload(draftId: string, titleForFilename: string) {
    try {
      await approveTailoredCv(draftId)
      const exported = await createCvExport(draftId)
      let exportStatus = exported.status
      let exportId = exported.id
      const start = Date.now()
      while (exportStatus !== "completed" && exportStatus !== "failed" && Date.now() - start < 60_000) {
        await new Promise((resolve) => setTimeout(resolve, 1500))
        const polled = await getExport(exportId)
        exportStatus = polled.status
        exportId = polled.id
      }
      if (exportStatus !== "completed") {
        throw new Error("Export is taking longer than expected. Try again from the CVs page.")
      }
      await downloadExport(exportId, `${titleForFilename.replace(/[^a-z0-9]+/gi, "-")}-tailored-cv.docx`)
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't finish preparing the tailored CV."))
    } finally {
      setIsDownloading(false)
      setTailoredJobId(null)
    }
  }

  useEffect(() => {
    if (!tailoredJobId) return
    if (tailoredPoll.isFailed) {
      toast.error("Building the tailored CV failed. Please try again.")
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setIsDownloading(false)
      setTailoredJobId(null)
      return
    }
    if (
      tailoredPoll.isCompleted &&
      tailoredPoll.job?.sourceEntityType === "tailored_cv_draft" &&
      handledDraftRef.current !== tailoredPoll.job.sourceEntityId
    ) {
      handledDraftRef.current = tailoredPoll.job.sourceEntityId
      void finishTailoredCvDownload(tailoredPoll.job.sourceEntityId, jobTitleForDownload)
    }
  }, [tailoredJobId, tailoredPoll.isCompleted, tailoredPoll.isFailed, tailoredPoll.job, jobTitleForDownload])

  if (matchQuery.isLoading) {
    return <div style={{ padding: 48, color: "var(--color-neutral-700)" }}>Loading…</div>
  }

  if (matchQuery.isError || !match) {
    return (
      <div style={{ padding: 48 }}>
        <p style={{ fontSize: 14, color: "var(--color-accent-700)" }}>
          {errorMessage(matchQuery.error, "Couldn't load this report.")}
        </p>
        <Link href="/dashboard" className="btn btn-ghost" style={{ marginTop: 16 }}>
          ← Overview
        </Link>
      </div>
    )
  }

  const jobTitle = match.jobTitle ?? matchListItem?.jobTitle ?? "Match report"
  const employer = match.employer ?? matchListItem?.employer
  const createdAt = match.createdAt ?? matchListItem?.createdAt
  const cvId = match.cvId ?? latestCv?.id
  const jobPostId = match.jobPostId ?? matchListItem?.jobPostId

  async function handleWriteCoverLetter() {
    if (!cvId || !jobPostId) {
      toast.error("We couldn't tell which CV and job this match used yet — open it from the CVs or Jobs page instead.")
      return
    }
    setIsStartingLetter(true)
    try {
      await startCoverLetterWorkflow({ cvId, jobPostId, matchId })
      router.push("/dashboard/cover-letters")
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't start the cover letter."))
    } finally {
      setIsStartingLetter(false)
    }
  }

  const atsIssues = match.atsIssues ?? []
  const formattingIssues = match.formattingIssues ?? []
  const tips = match.tips ?? []
  const atsFailCount = atsIssues.filter((i) => !i.passed).length
  const formattingFailCount = formattingIssues.filter((i) => !i.passed).length

  return (
    <div style={{ padding: 48, display: "flex", flexDirection: "column", gap: 40, maxWidth: 1000 }}>
      <div>
        <Link href="/dashboard" className="btn btn-ghost" style={{ marginBottom: 20 }}>
          ← Overview
        </Link>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 32 }}>
          <div>
            <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-accent)", marginBottom: 8 }}>
              Match report{createdAt ? ` · ${new Date(createdAt).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}` : ""}
            </div>
            <h1 style={{ fontSize: 42, margin: "0 0 8px" }}>{jobTitle.toUpperCase()}</h1>
            <p style={{ margin: 0, fontSize: 15, color: "var(--color-neutral-700)" }}>
              {employer ?? "Unknown employer"}
              {latestCv ? ` · ${latestCv.originalFilename}` : ""}
            </p>
          </div>
          <Tag variant="neutral">{match.status === "completed" ? "Match complete" : match.status}</Tag>
        </div>
      </div>

      <section style={{ background: "var(--color-surface)", padding: 32 }}>
        <h2 style={{ fontSize: 25, margin: "0 0 10px" }}>RESUME SUMMARY</h2>
        <p style={{ margin: "0 0 28px", fontSize: 14, lineHeight: 1.55, color: "var(--color-neutral-800)", maxWidth: "68ch" }}>
          Your score is benchmarked against 1m+ resumes at your career level and is based on 20+ key recruiter
          checks. The higher your resume score, the stronger your resume is and the more interviews you are likely
          to get.
        </p>
        {latestCv ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 36 }}>
            <ScoreCell label="Overall score" score={isScoring ? null : (analysis?.overallScore ?? null)} isLoading={isScoring} />
            <ScoreCell label="Skillset level vs market" score={isScoring ? null : (analysis?.skillsetScore ?? null)} isLoading={isScoring} />
            <ScoreCell label="Formatting" score={isScoring ? null : (analysis?.formattingScore ?? null)} isLoading={isScoring} />
          </div>
        ) : (
          <p style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>Resume score unavailable for this match.</p>
        )}
      </section>

      <EvidenceBand
        supported={match.supportedCount ?? 0}
        partial={match.partialCount ?? 0}
        unsupported={match.unsupportedCount ?? 0}
        contradictory={match.contradictoryCount ?? 0}
        unclear={match.unclearCount ?? 0}
      />

      <CollapsibleIssueSection
        title="ATS READINESS"
        countLabel={atsIssues.length > 0 ? `${atsFailCount} issue${atsFailCount === 1 ? "" : "s"} to fix` : undefined}
        description="Applicant tracking systems parse your file before a human sees it. These checks tell you what a parser would do with this file."
        issues={atsIssues}
      />

      <CollapsibleIssueSection
        title="FORMATTING"
        countLabel={formattingIssues.length > 0 ? `${formattingFailCount} issue${formattingFailCount === 1 ? "" : "s"} to fix` : undefined}
        description="Recruiters spend an average of 7 seconds on a first pass. These are the things that slow that pass down."
        issues={formattingIssues}
      />

      <CollapsibleIssueSection
        title="TIPS"
        countLabel={tips.length > 0 ? `${tips.length} suggestion${tips.length === 1 ? "" : "s"}` : undefined}
        countColor="var(--color-neutral-700)"
        tips={tips}
      />

      <div style={{ display: "flex", flexDirection: "column", gap: 12, paddingTop: 8 }}>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <button type="button" className="btn btn-primary" onClick={handleDownloadTailoredCv} disabled={isDownloading}>
            {isDownloading ? "Preparing…" : "Download tailored CV"}
          </button>
          <button type="button" className="btn btn-secondary" onClick={handleWriteCoverLetter} disabled={isStartingLetter}>
            {isStartingLetter ? "Starting…" : "Write the cover letter"}
          </button>
          <Link href="/dashboard/new" className="btn btn-secondary">
            Upload another file
          </Link>
        </div>
        <ProgressBar isActive={isDownloading} expectedDurationMs={20000} width={220} />
      </div>
    </div>
  )
}

function ScoreCell({
  label, score, isLoading,
}: {
  label: string
  score: number | null
  isLoading?: boolean
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-700)" }}>
        {label}
      </div>
      <ScoreBar score={score} isLoading={isLoading} />
    </div>
  )
}
