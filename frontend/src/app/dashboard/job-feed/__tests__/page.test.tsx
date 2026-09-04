import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import { createQueryWrapper } from "@/test/query-wrapper"
import JobFeedPage from "@/app/dashboard/job-feed/page"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

const BASE = "http://localhost:8000/api/v1"

function renderPage() {
  const Wrapper = createQueryWrapper()
  return render(
    <Wrapper>
      <JobFeedPage />
    </Wrapper>
  )
}

function posting(id: string, title: string) {
  return {
    id,
    source: "remoteok",
    title,
    company: "Acme",
    location: "Remote",
    remote: true,
    url: `https://example.com/${id}`,
    description: "A role.",
    tags: null,
    salaryText: null,
    postedAt: null,
    fetchedAt: new Date().toISOString(),
  }
}

/** Serves `total` postings, honouring the `limit` the page asks for, and
 *  records every limit requested so the test can assert the page actually
 *  paginates rather than just re-rendering. */
function paginatedFeed(total: number, requestedLimits: number[]) {
  return http.get(`${BASE}/job-feed`, ({ request }) => {
    const limit = Number(new URL(request.url).searchParams.get("limit") ?? 20)
    requestedLimits.push(limit)
    const items = Array.from({ length: Math.min(limit, total) }, (_, i) =>
      posting(`fp-${i + 1}`, `Role ${i + 1}`)
    )
    return HttpResponse.json({ items, total, limit, offset: 0 })
  })
}

describe("JobFeedPage load more", () => {
  it("shows Load more when more listings exist, and requests a larger page on click", async () => {
    const requestedLimits: number[] = []
    server.use(paginatedFeed(45, requestedLimits))

    renderPage()

    expect(await screen.findByText("Role 1")).toBeInTheDocument()
    // First page only.
    expect(screen.queryByText("Role 21")).not.toBeInTheDocument()
    expect(requestedLimits[0]).toBe(20)

    const button = screen.getByRole("button", { name: "Load more" })
    await userEvent.click(button)

    // Second page appended by the grown limit.
    expect(await screen.findByText("Role 21")).toBeInTheDocument()
    await waitFor(() => expect(requestedLimits).toContain(40))
  })

  it("hides Load more once every listing is on screen", async () => {
    const requestedLimits: number[] = []
    server.use(paginatedFeed(3, requestedLimits))

    renderPage()

    expect(await screen.findByText("Role 1")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument()
  })

  it("explains the cap instead of offering a Load more that would 422", async () => {
    // The API rejects limit > 100, so past the ceiling the page must stop
    // asking and say so rather than render a button that cannot work.
    const requestedLimits: number[] = []
    server.use(paginatedFeed(500, requestedLimits))

    renderPage()
    expect(await screen.findByText("Role 1")).toBeInTheDocument()

    for (let i = 0; i < 4; i++) {
      await userEvent.click(screen.getByRole("button", { name: "Load more" }))
      await waitFor(() => expect(screen.getByText(`Role ${(i + 2) * 20}`)).toBeInTheDocument())
    }

    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument()
    expect(
      screen.getByText(/Showing the first 100 of 500 listings/)
    ).toBeInTheDocument()
    expect(Math.max(...requestedLimits)).toBe(100)
  })
})
