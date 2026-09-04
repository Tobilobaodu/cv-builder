import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import { createQueryWrapper } from "@/test/query-wrapper"
import DashboardPage from "@/app/dashboard/page"
import { useAuthStore } from "@/store/auth-store"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

const BASE = "http://localhost:8000/api/v1"

function renderPage() {
  const Wrapper = createQueryWrapper()
  return render(
    <Wrapper>
      <DashboardPage />
    </Wrapper>
  )
}

const emptyList = () => HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })

describe("DashboardPage", () => {
  it("shows the current resume, stat band, and recent matches from real data", async () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })
    server.use(
      http.get(`${BASE}/cvs`, () =>
        HttpResponse.json({
          items: [
            {
              id: "cv-1",
              originalFilename: "resume.pdf",
              mimeType: "application/pdf",
              fileSizeBytes: 1024,
              status: "parsed",
              uploadStatus: "completed",
              processingStatus: "completed",
              jobStatus: null,
              resumeScore: 85,
              issueCount: 9,
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            },
          ],
          total: 1,
          limit: 20,
          offset: 0,
        })
      ),
      http.get(`${BASE}/cvs/cv-1/analysis`, () =>
        HttpResponse.json({}, { status: 404 })
      ),
      http.get(`${BASE}/job-posts`, () =>
        HttpResponse.json({
          items: [
            {
              id: "jp-1",
              sourceType: "text",
              sourceUrl: null,
              status: "completed",
              errorMessage: null,
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              profile: { jobTitle: "Senior Engineer", employer: "Acme" },
            },
          ],
          total: 2,
          limit: 20,
          offset: 0,
        })
      ),
      http.get(`${BASE}/matches`, () =>
        HttpResponse.json({
          items: [
            {
              id: "match-1",
              jobPostId: "jp-1",
              jobTitle: "Senior Engineer",
              employer: "Acme",
              status: "completed",
              score: 82,
              createdAt: new Date().toISOString(),
              completedAt: new Date().toISOString(),
            },
          ],
          total: 3,
          limit: 20,
          offset: 0,
        })
      ),
      http.get(`${BASE}/job-post-collections`, () => HttpResponse.json([]))
    )

    renderPage()

    expect(await screen.findByText("OVERVIEW")).toBeInTheDocument()
    expect(await screen.findByText("resume.pdf")).toBeInTheDocument()
    expect(await screen.findByText("Senior Engineer")).toBeInTheDocument()
    expect(screen.getByText("Acme")).toBeInTheDocument()
  })

  it("shows the first-run empty state when there are no CVs yet", async () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })
    server.use(
      http.get(`${BASE}/cvs`, emptyList),
      http.get(`${BASE}/job-posts`, emptyList),
      http.get(`${BASE}/matches`, emptyList),
      http.get(`${BASE}/job-post-collections`, () => HttpResponse.json([]))
    )

    renderPage()

    expect(await screen.findByText("NOTHING HERE YET. THAT'S THE POINT.")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /Start your first match/ })).toBeInTheDocument()
  })
})
