import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import { createQueryWrapper } from "@/test/query-wrapper"
import CoverLettersPage from "@/app/dashboard/cover-letters/page"

const BASE = "http://localhost:8000/api/v1"

function renderPage() {
  const Wrapper = createQueryWrapper()
  return render(
    <Wrapper>
      <CoverLettersPage />
    </Wrapper>
  )
}

describe("CoverLettersPage", () => {
  it("renders the list of cover-letter workflows returned by the API", async () => {
    server.use(
      http.get(`${BASE}/cover-letters`, () =>
        HttpResponse.json({
          items: [
            {
              id: "wf-1",
              jobPostId: "jp-1",
              jobTitle: "Senior Engineer",
              employer: "Acme",
              status: "approved",
              currentStep: 4,
              totalSteps: 4,
              createdAt: new Date().toISOString(),
            },
          ],
          total: 1,
          limit: 20,
          offset: 0,
        })
      )
    )

    renderPage()

    expect(await screen.findByText("Senior Engineer")).toBeInTheDocument()
    expect(screen.getByText("Acme")).toBeInTheDocument()
    expect(screen.getByText("Approved")).toBeInTheDocument()
  })

  it("shows the guided question step for an in-progress workflow", async () => {
    server.use(
      http.get(`${BASE}/cover-letters`, () =>
        HttpResponse.json({
          items: [
            {
              id: "wf-2",
              jobPostId: "jp-2",
              jobTitle: "Design Systems Lead",
              employer: "Wise",
              status: "awaiting_answers",
              currentStep: 2,
              totalSteps: 4,
              createdAt: new Date().toISOString(),
            },
          ],
          total: 1,
          limit: 20,
          offset: 0,
        })
      ),
      http.get(`${BASE}/cover-letters/wf-2/questions`, () =>
        HttpResponse.json([
          { id: "q-1", stepNumber: 2, questionText: "Why this company?", questionCategory: "motivation" },
        ])
      )
    )

    renderPage()

    expect(await screen.findByText("DESIGN SYSTEMS LEAD")).toBeInTheDocument()
    expect(await screen.findByText("Why this company?")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument()
  })

  it("shows an empty state when there are no workflows", async () => {
    server.use(
      http.get(`${BASE}/cover-letters`, () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })
      )
    )

    renderPage()

    expect(
      await screen.findByText("No cover letters yet — start one from a match's report page.")
    ).toBeInTheDocument()
  })

  it("shows an error message when the request fails", async () => {
    server.use(http.get(`${BASE}/cover-letters`, () => HttpResponse.json({}, { status: 500 })))

    renderPage()

    expect(
      await screen.findByText("Couldn't load this list. Please try again.")
    ).toBeInTheDocument()
  })
})
