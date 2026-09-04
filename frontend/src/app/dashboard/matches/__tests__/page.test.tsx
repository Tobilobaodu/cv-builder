import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import { createQueryWrapper } from "@/test/query-wrapper"
import MatchesPage from "@/app/dashboard/matches/page"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

const BASE = "http://localhost:8000/api/v1"

function renderPage() {
  const Wrapper = createQueryWrapper()
  return render(
    <Wrapper>
      <MatchesPage />
    </Wrapper>
  )
}

describe("MatchesPage", () => {
  it("renders the list of matches returned by the API", async () => {
    server.use(
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
          total: 1,
          limit: 20,
          offset: 0,
        })
      )
    )

    renderPage()

    expect(await screen.findByText("Senior Engineer")).toBeInTheDocument()
    expect(screen.getByText("Acme")).toBeInTheDocument()
    expect(screen.getByText("82")).toBeInTheDocument()
  })

  it("shows an empty state when there are no matches", async () => {
    server.use(
      http.get(`${BASE}/matches`, () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })
      )
    )

    renderPage()

    expect(
      await screen.findByText("No matches yet — upload a CV and a job to see how well they fit.")
    ).toBeInTheDocument()
  })

  it("shows an error message when the request fails", async () => {
    server.use(http.get(`${BASE}/matches`, () => HttpResponse.json({}, { status: 500 })))

    renderPage()

    expect(
      await screen.findByText("Couldn't load this list. Please try again.")
    ).toBeInTheDocument()
  })
})
