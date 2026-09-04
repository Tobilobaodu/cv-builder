import { apiFetch, apiFetchBlob, apiFetchStream } from "@/lib/api"

export type TrialSessionCreated = {
  trialSessionId: string
  expiresAt: string
}

export function createTrialSession() {
  return apiFetch<TrialSessionCreated>("/trial-sessions", { method: "POST" })
}

export type ClaimTrialResult = {
  claimed: boolean
  cvFilesReassigned: number
  jobPostsReassigned: number
  matchRunsReassigned: number
}

/** Call immediately after register/login when a trial session is active — reassigns its data to the new account. Requires the account's Bearer token (get_current_user-only). */
export function claimTrialSession(trialSessionId: string) {
  return apiFetch<ClaimTrialResult>("/auth/claim-trial", {
    method: "POST",
    body: { trialSessionId },
  })
}

export type CvUploadAccepted = {
  cvId: string
  processingJobId: string
  status: string
  filename: string
  fileSize: number
  mimeType: string
}

export function uploadCv(file: File) {
  const formData = new FormData()
  formData.append("file", file)
  return apiFetch<CvUploadAccepted>("/cvs", { method: "POST", body: formData })
}

export type JobPostAccepted = {
  jobPostId: string
  processingJobId: string
}

export function submitJobPostUrl(url: string) {
  return apiFetch<JobPostAccepted>("/job-posts/url", {
    method: "POST",
    body: { url },
  })
}

export function submitJobPostText(text: string) {
  return apiFetch<JobPostAccepted>("/job-posts/text", {
    method: "POST",
    body: { text },
  })
}

export type ProcessingJob = {
  id: string
  jobType: string
  sourceEntityType: string
  sourceEntityId: string
  status: "queued" | "processing" | "completed" | "failed" | "retrying"
  retryCount: number
  lastError: string | null
  createdAt: string
  completedAt: string | null
}

/** GET /jobs/{jobId} — the single source of truth for async job status; poll here, never infer from a domain resource's own status field. */
export function getJob(jobId: string) {
  return apiFetch<ProcessingJob>(`/jobs/${jobId}`)
}

export type ParsedCvProfile = {
  cvId: string
  profileVersionId: string
  versionNumber: number
  structuredPayload: {
    basics?: { summary?: string }
    [key: string]: unknown
  }
}

export function getParsedCvProfile(cvId: string) {
  return apiFetch<ParsedCvProfile>(`/cvs/${cvId}/parsed-profile`)
}

export type JobPostProfile = {
  jobTitle: string | null
  employer: string | null
  location: string | null
  requiredSkills: string[] | null
  preferredSkills: string[] | null
  responsibilities: string[] | null
  qualifications: string[] | null
  keywords: string[] | null
  seniority: string | null
  confidence: number | null
}

export type JobPostDetail = {
  id: string
  sourceType: string
  sourceUrl: string | null
  /** Written by the job_fetch worker. Present from status "structuring"
   *  onward — i.e. before job_post_parse has finished, so a caller that
   *  only wants the text does not have to wait for "completed". */
  rawText: string
  status: string
  errorMessage: string | null
  profile: JobPostProfile | null
}

export function getJobPost(jobPostId: string) {
  return apiFetch<JobPostDetail>(`/job-posts/${jobPostId}`)
}

export type MatchAccepted = {
  matchId: string
  processingJobId: string
}

export function createMatch(cvProfileVersionId: string, jobPostId: string) {
  return apiFetch<MatchAccepted>("/matches", {
    method: "POST",
    body: { cvProfileVersionId, jobPostId },
  })
}

export type MatchIssue = { passed: boolean; severity: string; title: string; detail: string }

export type MatchResult = {
  id: string
  status: string
  score: number | null
  supportedCount: number | null
  partialCount: number | null
  unsupportedCount: number | null
  /** Being added alongside atsIssues/formattingIssues/tips — optional
   *  until the backend extension lands; render as 0 rather than crash. */
  contradictoryCount?: number | null
  unclearCount?: number | null
  totalRequirements: number | null
  summaryAnalysis: string | null
  /** Report-detail's ATS Readiness / Formatting / Tips sections. Same
   *  shape as GET /cvs/{id}/analysis's issue lists. Optional until the
   *  backend extension lands. */
  atsIssues?: MatchIssue[]
  formattingIssues?: MatchIssue[]
  tips?: string[]
  /** Kept optional and read defensively even though the backend now
   *  always populates these for a resolved match_run — an older/failed
   *  run can still legitimately have nulls here. When absent, the Report
   *  detail page falls back to whatever it can resolve from
   *  listMatches()'s cache for the header, and hides the "resume summary"
   *  section rather than guessing a CV id. */
  jobPostId?: string
  cvId?: string
  jobTitle?: string | null
  employer?: string | null
  createdAt?: string
}

export function getMatch(matchId: string) {
  return apiFetch<MatchResult>(`/matches/${matchId}`)
}

export type ProcessingJobRef = {
  jobId: string
  status: string
}

export function createTailoredCv(matchId: string) {
  return apiFetch<ProcessingJobRef>(`/matches/${matchId}/tailored-cv`, {
    method: "POST",
  })
}

export type TailoredCvSection = {
  id: string
  sectionType: string
  contentText: string
  orderIndex: number
}

export type TailoredCvDraft = {
  id: string
  matchRunId: string
  versionNumber: number
  status: string
  sections: TailoredCvSection[]
  improvementChecklist: string[] | null
}

export function getTailoredCvDraft(draftId: string) {
  return apiFetch<TailoredCvDraft>(`/tailored-cvs/${draftId}`)
}

/** Required before export — app/api/v1/exports.py rejects a non-'approved' draft with 409. */
export function approveTailoredCv(draftId: string) {
  return apiFetch<TailoredCvDraft>(`/tailored-cvs/${draftId}/approve`, {
    method: "POST",
  })
}

export type ExportRequestOut = {
  id: string
  status: string
  format: string
}

export function createCvExport(draftId: string, templateId?: string) {
  return apiFetch<ExportRequestOut>(`/exports/cv/${draftId}`, {
    method: "POST",
    body: templateId ? { templateId } : {},
  })
}

export function getExport(exportId: string) {
  return apiFetch<ExportRequestOut>(`/exports/${exportId}`)
}

/**
 * The download endpoint is re-checked (auth/trial header) on every request —
 * it can't be a plain <a href>, which would send no identity header at all.
 * Fetches the file with the right header, then triggers a normal browser
 * save via a temporary object URL.
 */
export async function downloadExport(exportId: string, filename: string) {
  const blob = await apiFetchBlob(`/exports/${exportId}/download`)
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export type ExportTemplate = {
  id: string
  name: string
  description: string
}

export function listExportTemplates() {
  return apiFetch<ExportTemplate[]>("/exports/templates")
}

export function exportCoverLetter(workflowId: string, templateId?: string) {
  return apiFetch<ExportRequestOut>(`/exports/cover-letter/${workflowId}`, {
    method: "POST",
    body: templateId ? { templateId } : {},
  })
}

export function exportApplicationPack(
  tailoredCvDraftId: string,
  coverLetterWorkflowId: string,
  templateId?: string
) {
  return apiFetch<ExportRequestOut>("/exports/application-pack", {
    method: "POST",
    body: {
      tailoredCvDraftId,
      coverLetterWorkflowId,
      ...(templateId ? { templateId } : {}),
    },
  })
}

/** Derives a PDF version of an already-downloaded docx export — must call
 * downloadExport() on the source export first (see exports.py::export_pdf's
 * precondition). Returns a new Export, polled/downloaded the same way. */
export function exportPdf(exportId: string) {
  return apiFetch<ExportRequestOut>(`/exports/${exportId}/pdf`, { method: "POST" })
}

export type AtsCheckItem = {
  checkType: string
  passed: boolean
  severity: string
  detail: string
}

export type AtsReadinessCheckResponse = {
  id: string
  cvId: string
  cvProfileVersionId: string | null
  overallScore: number
  contactInfoParseable: boolean | null
  checks: AtsCheckItem[]
  createdAt: string
}

export function triggerAtsCheck(cvId: string) {
  return apiFetch<ProcessingJobRef>(`/cvs/${cvId}/ats-check`, {
    method: "POST",
  })
}

/** 404s until a check has run — callers should treat that as "no result yet", not an error. */
export function getAtsCheck(cvId: string) {
  return apiFetch<AtsReadinessCheckResponse>(`/cvs/${cvId}/ats-check`)
}

export type JobPostCollection = {
  id: string
  name: string
  jobPostIds: string[]
  createdAt: string
  updatedAt: string
}

export function createJobPostCollection(name: string, jobPostIds: string[]) {
  return apiFetch<JobPostCollection>("/job-post-collections", {
    method: "POST",
    body: { name, jobPostIds },
  })
}

export function listJobPostCollections() {
  return apiFetch<JobPostCollection[]>("/job-post-collections")
}

export function triggerCoverageReport(collectionId: string, cvId: string) {
  return apiFetch<ProcessingJobRef>(
    `/job-post-collections/${collectionId}/coverage-report`,
    { method: "POST", body: { cvId } }
  )
}

export type AggregateGap = {
  requirementTextCluster: string
  recurrenceCount: number
  recurrenceRatio: number
  affectedJobPostIds: string[]
  currentSupportLevelDistribution: Record<string, number>
}

export type CoverageReport = {
  id: string
  cvProfileVersionId: string
  collectionId: string
  matchRunIds: string[]
  status: string
  aggregateGaps: AggregateGap[]
  skippedJobPostIds: string[] | null
  createdAt: string
  completedAt: string | null
}

export function getCoverageReport(reportId: string) {
  return apiFetch<CoverageReport>(`/coverage-reports/${reportId}`)
}

// ── Single-call tailored CV (replaces the match → tailored-cv chain) ──
// The job description is sent as raw text alongside the CV's extracted
// text: the model does its own requirement extraction, so there is no
// job-post parse or match step in this flow.

export type CvRawText = {
  cvId: string
  canonicalText: string
  characters: number
  mergeStrategy: string | null
  ocrUsed: boolean
}

export function getCvRawText(cvId: string) {
  return apiFetch<CvRawText>(`/cvs/${cvId}/raw-text`)
}

/** Deterministic, synonym-blind keyword-in-text check (jbs-solution-
 *  sheet.md Q1) — the strict bar a Taleo/Lever-class ATS keyword filter
 *  applies, shown alongside atsScore's semantic judgement rather than
 *  instead of it. */
export type LiteralCoverage = {
  coverage: number
  present: string[]
  absent: string[]
}

export type MatchAnalysisStats = {
  /** What the CV evidences vs what the role is, so a capped score can
   *  explain itself rather than looking arbitrary. */
  cvOccupation: string
  jobOccupation: string
  sameOccupation: boolean
  atsScore: number
  matchLabel: string
  matchedSkills: string[]
  transferableSkills: string[]
  missingSkills: string[]
  priorityKeywords: string[]
  literalCoverage: LiteralCoverage
}

export type MatchAnalysisResult = {
  matchNotes: string[]
  informationNeeded: string[]
  stats: MatchAnalysisStats
  promptVersion: string
}

/** Score, gaps and tips — small and fast, returned well before the
 *  tailored CV finishes streaming (see streamResumeRewrite below). Split
 *  from the old single /resume-rewrites call per jbs-solution-sheet.md S1:
 *  this payload is useless-until-complete JSON, so it's a plain POST, not
 *  streamed. */
export function createMatchAnalysis(input: {
  cvId: string
  jobDescription: string
  targetTitle?: string
}) {
  return apiFetch<MatchAnalysisResult>("/match-analyses", {
    method: "POST",
    body: input,
  })
}

/** Renders the tailored CV Markdown to a PDF. The rewrite is stateless, so
 *  the Markdown is posted back rather than referenced by id. */
export function downloadResumePdf(input: {
  tailoredResumeMarkdown: string
  fileName?: string
}) {
  return apiFetchBlob("/resume-rewrites/pdf", {
    method: "POST",
    body: input,
  })
}

/** One item from streamResumeRewrite's async generator — mirrors the
 *  backend's RewriteStreamEvent (see resume_rewrite.py). "delta": append
 *  `text`. "corrected": *replace* everything rendered so far with `text`
 *  — rare, only fires if a truthfulness safety net caught something after
 *  it had already streamed (see that file's docstring). "done"/"error"
 *  end the stream. */
export type RewriteStreamEvent =
  | { type: "delta"; text: string }
  | { type: "corrected"; text: string; informationNeeded: string[] }
  | { type: "done"; text: string; informationNeeded: string[] }
  | { type: "error"; detail: string }

/** Streams the tailored CV as markdown (jbs-solution-sheet.md S2) —
 *  readable as it arrives, unlike the analysis half's JSON. Pass the
 *  MatchAnalysisResult from createMatchAnalysis as `analysis` so the
 *  rewrite is grounded in the same assessment already on screen; the
 *  backend only reads its `.stats`, so passing the whole result through
 *  as-is is fine. */
export async function* streamResumeRewrite(input: {
  cvId: string
  jobDescription: string
  targetTitle?: string
  candidateNotes?: string
  analysis?: MatchAnalysisResult | null
}): AsyncGenerator<RewriteStreamEvent> {
  for await (const raw of apiFetchStream("/resume-rewrites", {
    method: "POST",
    body: input,
  })) {
    yield JSON.parse(raw) as RewriteStreamEvent
  }
}

// recordJourney moved to api.ts (both the trial and dashboard flows fire
// it now) — re-exported here so existing `from "@/lib/trial-api"` imports
// keep working.
export { recordJourney } from "@/lib/api"

// ── CV analysis (resume score / ATS readiness / formatting / tips) ──
// Powers the Overview and Report-detail "resume summary" ScoreBar trio.
// GET 404s until an analysis has run for this CV — callers should trigger
// one (POST, same path) and poll, the same accepted-job pattern as
// triggerAtsCheck/getAtsCheck above.

export type CvAnalysisIssue = { passed: boolean; severity: string; title: string; detail: string }

export type CvAnalysis = {
  overallScore: number
  skillsetScore: number
  formattingScore: number
  atsIssues: CvAnalysisIssue[]
  formattingIssues: CvAnalysisIssue[]
  tips: string[]
}

/** 404s until an analysis has run — callers should treat that as "not scored yet", not an error. */
export function getCvAnalysis(cvId: string) {
  return apiFetch<CvAnalysis>(`/cvs/${cvId}/analysis`)
}

export function triggerCvAnalysis(cvId: string) {
  return apiFetch<ProcessingJobRef>(`/cvs/${cvId}/analysis`, { method: "POST" })
}

// ── Cover letter workflow (guided Q&A) ──
// app/api/v1/cover_letters.py: POST /start, GET .../questions,
// POST .../answers, GET .../draft, POST .../regenerate, POST .../approve.
// Account-only (no trial_session_id path) — matches the backend router's
// own get_current_user-only gate.

export type CoverLetterWorkflow = {
  id: string
  cvId: string
  jobPostId: string
  matchId: string | null
  currentStep: number
  /** total_steps is moving from 3 to 4 on the backend (in flight) —
   *  optional here so this stays correct either way; read it, never
   *  hardcode a step count. */
  totalSteps?: number
  status: string
  questionSetVersion: number
  createdAt: string
}

export function startCoverLetterWorkflow(input: { cvId: string; jobPostId: string; matchId?: string }) {
  return apiFetch<CoverLetterWorkflow>("/cover-letters/start", {
    method: "POST",
    body: input,
  })
}

export type CoverLetterQuestion = {
  id: string
  stepNumber: number
  questionText: string
  questionCategory: string
}

export function getCoverLetterQuestions(workflowId: string) {
  return apiFetch<CoverLetterQuestion[]>(`/cover-letters/${workflowId}/questions`)
}

export function submitCoverLetterAnswers(
  workflowId: string,
  answers: { questionId: string; answerText: string }[]
) {
  return apiFetch<CoverLetterWorkflow>(`/cover-letters/${workflowId}/answers`, {
    method: "POST",
    body: { answers },
  })
}

export type CoverLetterDraft = {
  id: string
  workflowId: string
  versionNumber: number
  status: string
  bodyText: string
  evidenceReferences: string[] | null
  promptVersion: string | null
  modelId: string | null
  createdAt: string
  approvedAt: string | null
}

/** 404s until every step is answered and generation has finished. */
export function getCoverLetterDraft(workflowId: string) {
  return apiFetch<CoverLetterDraft>(`/cover-letters/${workflowId}/draft`)
}

export function regenerateCoverLetter(workflowId: string) {
  return apiFetch<ProcessingJobRef>(`/cover-letters/${workflowId}/regenerate`, { method: "POST" })
}

export function approveCoverLetterDraft(workflowId: string) {
  return apiFetch<CoverLetterDraft>(`/cover-letters/${workflowId}/approve`, { method: "POST" })
}
