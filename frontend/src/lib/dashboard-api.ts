import { apiFetch } from "@/lib/api"

export type CvFileListItem = {
  id: string
  originalFilename: string
  mimeType: string
  fileSizeBytes: number
  status: string
  uploadStatus: string
  processingStatus: string
  jobStatus: string | null
  createdAt: string
  updatedAt: string
  /** Absent until GET /cvs/{id}/analysis has run for this CV — render a
   *  "Scoring…" state rather than treating undefined as zero. */
  resumeScore?: number
  issueCount?: number
}

export type CvListResponse = {
  items: CvFileListItem[]
  total: number
  limit: number
  offset: number
}

export function listCvs(limit = 20, offset = 0) {
  return apiFetch<CvListResponse>(`/cvs?limit=${limit}&offset=${offset}`)
}

export function deleteCv(cvId: string) {
  return apiFetch<void>(`/cvs/${cvId}`, { method: "DELETE" })
}

export type JobPostProfileSummary = {
  jobTitle: string | null
  employer: string | null
}

export type JobPostListItem = {
  id: string
  sourceType: string
  sourceUrl: string | null
  status: string
  errorMessage: string | null
  createdAt: string
  updatedAt: string
  profile: JobPostProfileSummary | null
}

export type JobPostListResponse = {
  items: JobPostListItem[]
  total: number
  limit: number
  offset: number
}

export function listJobPosts(limit = 20, offset = 0) {
  return apiFetch<JobPostListResponse>(`/job-posts?limit=${limit}&offset=${offset}`)
}

export function deleteJobPost(jobPostId: string) {
  return apiFetch<void>(`/job-posts/${jobPostId}`, { method: "DELETE" })
}

export type MatchListItem = {
  id: string
  jobPostId: string
  jobTitle: string | null
  employer: string | null
  status: string
  score: number | null
  createdAt: string
  completedAt: string | null
}

export type MatchListResponse = {
  items: MatchListItem[]
  total: number
  limit: number
  offset: number
}

export function listMatches(limit = 20, offset = 0) {
  return apiFetch<MatchListResponse>(`/matches?limit=${limit}&offset=${offset}`)
}

export function deleteMatch(matchId: string) {
  return apiFetch<void>(`/matches/${matchId}`, { method: "DELETE" })
}

export type CoverLetterWorkflowListItem = {
  id: string
  jobPostId: string
  jobTitle: string | null
  employer: string | null
  status: string
  currentStep: number
  totalSteps: number
  createdAt: string
}

export type CoverLetterWorkflowListResponse = {
  items: CoverLetterWorkflowListItem[]
  total: number
  limit: number
  offset: number
}

export function listCoverLetterWorkflows(limit = 20, offset = 0) {
  return apiFetch<CoverLetterWorkflowListResponse>(
    `/cover-letters?limit=${limit}&offset=${offset}`
  )
}

export function deleteCoverLetterWorkflow(workflowId: string) {
  return apiFetch<void>(`/cover-letters/${workflowId}`, { method: "DELETE" })
}
