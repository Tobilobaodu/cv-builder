"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { ArrowRight } from "lucide-react"

import { listCvs, listJobPosts, listMatches } from "@/lib/dashboard-api"
import { listJobPostCollections } from "@/lib/trial-api"
import { useCvAnalysis } from "@/hooks/use-cv-analysis"
import { ScoreBar } from "@/components/modernist/score-bar"
import { StatBand } from "@/components/modernist/stat-band"
import { Tag } from "@/components/modernist/tag"
import { TableShell } from "@/components/modernist/table-shell"

const TRIAL_REWRITES_TOTAL = 3
// A match below this score is treated as "would need work before applying"
// for the stat band's "blocking N applications" figure — the mockup shows
// this as a derived insight but the backend has no such flag, so this is a
// client-side heuristic, not a real blocking rule.
const BLOCKING_SCORE_THRESHOLD = 70

export default function DashboardPage() {
  const router = useRouter()

  const cvsQuery = useQuery({ queryKey: ["dashboard-cvs"], queryFn: () => listCvs() })
  const jobsQuery = useQuery({ queryKey: ["dashboard-job-posts"], queryFn: () => listJobPosts() })
  const matchesQuery = useQuery({ queryKey: ["dashboard-matches"], queryFn: () => listMatches() })
  const collectionsQuery = useQuery({
    queryKey: ["dashboard-job-post-collections"],
    queryFn: () => listJobPostCollections(),
  })

  const latestCv = cvsQuery.data?.items[0]
  const { analysis, isScoring } = useCvAnalysis(latestCv?.id ?? null)

  const isLoaded = cvsQuery.isSuccess
  const hasNoCvs = isLoaded && (cvsQuery.data?.items.length ?? 0) === 0

  if (cvsQuery.isLoading) {
    return (
      <div style={{ padding: 48, color: "var(--color-neutral-700)" }}>Loading…</div>
    )
  }

  if (hasNoCvs) {
    return <FirstRun />
  }

  const matches = matchesQuery.data?.items ?? []
  const scoredMatches = matches.filter((m) => m.score != null)
  const avgMatchRate =
    scoredMatches.length > 0
      ? Math.round(scoredMatches.reduce((sum, m) => sum + (m.score ?? 0), 0) / scoredMatches.length)
      : null
  const blockingCount = scoredMatches.filter((m) => (m.score ?? 0) < BLOCKING_SCORE_THRESHOLD).length
  const issueCount = latestCv?.issueCount ?? (analysis ? analysis.atsIssues.filter((i) => !i.passed).length + analysis.formattingIssues.filter((i) => !i.passed).length : null)
  const jobsParsing = (jobsQuery.data?.items ?? []).filter((j) => j.status !== "completed" && j.status !== "failed").length
  const rewritesLeft = Math.max(0, TRIAL_REWRITES_TOTAL - (matchesQuery.data?.total ?? 0))

  const recentMatches = matches.slice(0, 3)
  const hasCollections = (collectionsQuery.data?.length ?? 0) > 0

  return (
    <div style={{ padding: 48, display: "flex", flexDirection: "column", gap: 48, maxWidth: 1180 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 32 }}>
        <div>
          <h1 style={{ fontSize: 42, margin: "0 0 8px" }}>OVERVIEW</h1>
          <p style={{ margin: 0, fontSize: 15, color: "var(--color-neutral-700)", maxWidth: "52ch" }}>
            Your latest resume, scored against 1m+ resumes at your career level on 20+ recruiter checks.
          </p>
        </div>
        <Link href="/dashboard/new" className="btn btn-primary" style={{ flex: "none" }}>
          New match
          <ArrowRight width={16} height={16} strokeWidth={2.2} strokeLinecap="square" />
        </Link>
      </div>

      {/* resume summary */}
      <section style={{ background: "var(--color-surface)", padding: 32 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 24, marginBottom: 28 }}>
          <div>
            <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-600)", marginBottom: 6 }}>
              Current resume
            </div>
            <h2 style={{ fontSize: 25, margin: 0 }}>{latestCv?.originalFilename ?? "—"}</h2>
          </div>
          {latestCv && (
            <div style={{ fontSize: 12, color: "var(--color-neutral-700)" }}>
              Uploaded {new Date(latestCv.createdAt).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
            </div>
          )}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 40 }}>
          <ScoreCell label="Overall score" score={isScoring ? null : (analysis?.overallScore ?? null)} note={analysis ? "Ranked against resumes in your industry" : undefined} isLoading={isScoring} />
          <ScoreCell label="Skillset level vs market" score={isScoring ? null : (analysis?.skillsetScore ?? null)} isLoading={isScoring} />
          <ScoreCell
            label="Formatting"
            score={isScoring ? null : (analysis?.formattingScore ?? null)}
            note={analysis ? `${analysis.atsIssues.filter((i) => !i.passed).length + analysis.formattingIssues.filter((i) => !i.passed).length} fixable issues across ATS and layout` : undefined}
            isLoading={isScoring}
          />
        </div>

        <div style={{ marginTop: 32, display: "flex", gap: 12 }}>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!latestCv}
            onClick={() => {
              const latest = matches[0]
              if (latest) router.push(`/dashboard/matches/${latest.id}`)
            }}
          >
            View full report
            <ArrowRight width={16} height={16} strokeWidth={2.2} strokeLinecap="square" />
          </button>
          <Link href="/dashboard/new" className="btn btn-secondary">
            Upload another file
          </Link>
        </div>
      </section>

      <StatBand
        stats={[
          {
            label: "Avg. match rate",
            value: avgMatchRate != null ? `${avgMatchRate}%` : "—",
            note: `across ${scoredMatches.length} match${scoredMatches.length === 1 ? "" : "es"}`,
          },
          {
            label: "ATS readiness",
            value: (
              <>
                {issueCount ?? "—"}
                <span style={{ fontSize: 15, color: "var(--color-neutral-600)" }}> issues</span>
              </>
            ),
            note: blockingCount > 0 ? `Blocking ${blockingCount} application${blockingCount === 1 ? "" : "s"}` : "Nothing blocking",
            noteColor: blockingCount > 0 ? "var(--color-accent-700)" : undefined,
          },
          {
            label: "Jobs saved",
            value: jobsQuery.data?.total ?? "—",
            note: jobsParsing > 0 ? `${jobsParsing} still parsing` : undefined,
          },
          {
            label: "Rewrites left",
            value: rewritesLeft,
            note: "Free trial",
          },
        ]}
      />

      <section>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 16 }}>
          <h3 style={{ fontSize: 20, margin: 0 }}>RECENT MATCHES</h3>
          <Link href="/dashboard/jobs" className="btn btn-ghost">
            All jobs
          </Link>
        </div>
        <TableShell
          isLoading={matchesQuery.isLoading}
          isError={matchesQuery.isError}
          isEmpty={!!matchesQuery.data && matchesQuery.data.items.length === 0}
          emptyMessage="No matches yet — start a new match to see how your CV fits a role."
        >
          <thead>
            <tr>
              <th>Role</th>
              <th>Employer</th>
              <th style={{ width: 200 }}>Match</th>
              <th>Evidence</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {recentMatches.map((match) => {
              const completed = match.status === "completed"
              return (
                <tr
                  key={match.id}
                  className={completed ? "rowlink" : undefined}
                  style={completed ? { cursor: "pointer" } : undefined}
                  onClick={completed ? () => router.push(`/dashboard/matches/${match.id}`) : undefined}
                >
                  <td style={{ fontWeight: 600 }}>{match.jobTitle ?? "Untitled role"}</td>
                  <td>{match.employer ?? "—"}</td>
                  <td>
                    <ScoreBar score={match.score} size="sm" isLoading={!completed && match.status !== "failed"} />
                  </td>
                  <td style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>
                    {completed ? "Scored" : "Scoring…"}
                  </td>
                  <td>
                    <Tag variant={completed ? "neutral" : "accent"}>{completed ? "Tailored" : "In progress"}</Tag>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </TableShell>
      </section>

      <section>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 16 }}>
          <h3 style={{ fontSize: 20, margin: 0 }}>RECURRING GAPS</h3>
          <div style={{ fontSize: 12, color: "var(--color-neutral-700)" }}>
            Across your {jobsQuery.data?.total ?? 0} saved jobs
          </div>
        </div>
        {hasCollections ? (
          <p style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>
            Open <Link href="/dashboard/jobs">Jobs</Link> to see your latest coverage report.
          </p>
        ) : (
          <p style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>
            Select two or more jobs on the <Link href="/dashboard/jobs">Jobs</Link> page to run a coverage report and
            see the requirements that keep coming up.
          </p>
        )}
      </section>
    </div>
  )
}

function ScoreCell({
  label, score, note, isLoading,
}: {
  label: string
  score: number | null
  note?: string
  isLoading?: boolean
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-700)" }}>
        {label}
      </div>
      <ScoreBar score={score} note={note} isLoading={isLoading} />
    </div>
  )
}

function FirstRun() {
  return (
    <div style={{ padding: 48, maxWidth: 1000 }}>
      <div style={{ borderBottom: "1px solid var(--color-divider)", paddingBottom: 40, marginBottom: 40 }}>
        <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-accent)", marginBottom: 12 }}>
          Welcome
        </div>
        <h1 style={{ fontSize: 42, margin: "0 0 16px", maxWidth: "22ch" }}>NOTHING HERE YET. THAT&apos;S THE POINT.</h1>
        <p style={{ margin: 0, fontSize: 15, lineHeight: 1.6, color: "var(--color-neutral-800)", maxWidth: "58ch" }}>
          Upload one CV and paste one job posting. You&apos;ll see your resume score, every requirement the posting
          asks for, and which of them your experience actually backs up.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 2, background: "var(--color-divider)", marginBottom: 40 }}>
        {[
          { n: "01", title: "Upload", body: "PDF or DOCX. We read the structure, not just the words." },
          { n: "02", title: "Add the job", body: "Paste the posting or drop a link. Every requirement gets pulled out." },
          { n: "03", title: "Fix and apply", body: "A scored report, a tailored file, and a cover letter if you want one." },
        ].map((step, i) => (
          <div
            key={step.n}
            style={{
              background: "var(--color-bg)",
              padding: i === 0 ? "24px 24px 24px 0" : i === 2 ? "24px 0 24px 24px" : "24px",
            }}
          >
            <div style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 13, color: "var(--color-accent)", marginBottom: 12 }}>
              {step.n}
            </div>
            <div style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 17, marginBottom: 6 }}>{step.title}</div>
            <div style={{ fontSize: 13, lineHeight: 1.5, color: "var(--color-neutral-700)" }}>{step.body}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <Link href="/dashboard/new" className="btn btn-primary">
          Start your first match
          <ArrowRight width={16} height={16} strokeWidth={2.2} strokeLinecap="square" />
        </Link>
      </div>
    </div>
  )
}
