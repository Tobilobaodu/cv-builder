import { apiFetch } from "@/lib/api"

export type FeedJobPosting = {
  id: string
  source: string
  title: string
  company: string | null
  location: string | null
  remote: boolean | null
  url: string
  description: string
  tags: string[] | null
  salaryText: string | null
  postedAt: string | null
  fetchedAt: string
}

export type FeedJobPostingListResponse = {
  items: FeedJobPosting[]
  total: number
  limit: number
  offset: number
}

export type FeedImportAccepted = {
  jobPostId: string
  processingJobId: string
}

export function listJobFeed(params?: {
  q?: string
  location?: string
  remote?: boolean
  source?: string
  limit?: number
  offset?: number
}) {
  const query = new URLSearchParams()
  if (params?.q) query.set("q", params.q)
  if (params?.location) query.set("location", params.location)
  if (params?.remote !== undefined) query.set("remote", String(params.remote))
  if (params?.source) query.set("source", params.source)
  query.set("limit", String(params?.limit ?? 20))
  query.set("offset", String(params?.offset ?? 0))
  return apiFetch<FeedJobPostingListResponse>(`/job-feed?${query.toString()}`)
}

export function importJobFeedPosting(feedPostingId: string) {
  return apiFetch<FeedImportAccepted>(`/job-feed/${feedPostingId}/import`, { method: "POST" })
}
