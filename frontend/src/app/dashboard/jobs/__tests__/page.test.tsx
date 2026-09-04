import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import { createQueryWrapper } from "@/test/query-wrapper"
import JobsPage from "@/app/dashboard/jobs/page"

const BASE = "http://localhost:8000/api/v1"
const emptyMatches = () => HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })

function renderPage() {
  const Wrapper = createQueryWrapper()
  return render(
    <Wrapper>
      <JobsPage />
    </Wrapper>
  )
}

describe("JobsPage", () => {
  it("renders the list of job posts returned by the API", async () => {
    server.use(
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
          total: 1,
          limit: 20,
          offset: 0,
        })
      ),
      http.get(`${BASE}/matches`, emptyMatches)
    )

    renderPage()

    expect(await screen.findByText("Senior Engineer")).toBeInTheDocument()
    expect(screen.getByText("Acme")).toBeInTheDocument()
    expect(screen.getByText("Completed")).toBeInTheDocument()
  })

  it("shows an empty state when there are no jobs", async () => {
    server.use(
      http.get(`${BASE}/job-posts`, () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })
      ),
      http.get(`${BASE}/matches`, emptyMatches)
    )

    renderPage()

    expect(
      await screen.findByText(
        "You haven't saved any jobs yet — paste a job link or description to get started."
      )
    ).toBeInTheDocument()
  })

  it("shows a 'paste the text instead' affordance for a failed job post", async () => {
    server.use(
      http.get(`${BASE}/job-posts`, () =>
        HttpResponse.json({
          items: [
            {
              id: "jp-2",
              sourceType: "url",
              sourceUrl: "https://example.com/job",
              status: "failed",
              errorMessage: "blocked",
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              profile: null,
            },
          ],
          total: 1,
          limit: 20,
          offset: 0,
        })
      ),
      http.get(`${BASE}/matches`, emptyMatches)
    )

    renderPage()

    expect(await screen.findByText("Couldn't read this posting")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Paste the text instead" })).toBeInTheDocument()
  })
})
