import { apiFetch } from "@/lib/api"

export type ApplicationStatus =
  | "applied"
  | "interviewing"
  | "offer"
  | "accepted"
  | "rejected"
  | "withdrawn"
  | "ghosted"

export type ApplicationEvent = {
  id: string
  eventType: "status_change" | "note_added"
  fromStatus: ApplicationStatus | null
  toStatus: ApplicationStatus | null
  note: string | null
  actorType: "user" | "system"
  createdAt: string
}

export type ApplicationListItem = {
  id: string
  jobPostId: string | null
  jobTitle: string
  employer: string
  status: ApplicationStatus
  appliedAt: string
  createdAt: string
  updatedAt: string
}

export type Application = ApplicationListItem & {
  tailoredCvDraftId: string | null
  coverLetterDraftId: string | null
  notes: string | null
  events: ApplicationEvent[]
}

export type ApplicationListResponse = {
  items: ApplicationListItem[]
  total: number
  limit: number
  offset: number
}

export type ApplicationStats = {
  total: number
  byStatus: Record<string, number>
  responseRate: number | null
}

export function listApplications(params?: { limit?: number; offset?: number; status?: string }) {
  const query = new URLSearchParams()
  query.set("limit", String(params?.limit ?? 20))
  query.set("offset", String(params?.offset ?? 0))
  if (params?.status) query.set("status", params.status)
  return apiFetch<ApplicationListResponse>(`/applications?${query.toString()}`)
}

export function getApplicationStats() {
  return apiFetch<ApplicationStats>(`/applications/stats`)
}

export function getApplication(applicationId: string) {
  return apiFetch<Application>(`/applications/${applicationId}`)
}

export function createApplication(body: {
  jobPostId?: string
  tailoredCvDraftId?: string
  coverLetterDraftId?: string
  jobTitle: string
  employer: string
  appliedAt?: string
  notes?: string
}) {
  return apiFetch<Application>(`/applications`, { method: "POST", body })
}

export function updateApplicationStatus(applicationId: string, status: ApplicationStatus, note?: string) {
  return apiFetch<Application>(`/applications/${applicationId}/status`, {
    method: "PATCH",
    body: { status, note },
  })
}

export function addApplicationNote(applicationId: string, note: string) {
  return apiFetch<Application>(`/applications/${applicationId}/notes`, {
    method: "POST",
    body: { note },
  })
}

export function deleteApplication(applicationId: string) {
  return apiFetch<void>(`/applications/${applicationId}`, { method: "DELETE" })
}
