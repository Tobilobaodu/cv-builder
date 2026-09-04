import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import { createQueryWrapper } from "@/test/query-wrapper"
import CvsPage from "@/app/dashboard/cvs/page"

const BASE = "http://localhost:8000/api/v1"

function renderPage() {
  const Wrapper = createQueryWrapper()
  return render(
    <Wrapper>
      <CvsPage />
    </Wrapper>
  )
}

describe("CvsPage", () => {
  it("renders the list of CVs returned by the API, with the newest tagged Current", async () => {
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
      http.get(`${BASE}/matches`, () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })
      )
    )

    renderPage()

    expect(await screen.findByText("resume.pdf")).toBeInTheDocument()
    expect(screen.getByText("Parsed")).toBeInTheDocument()
    expect(screen.getByText("Current")).toBeInTheDocument()
    expect(screen.getByText("9")).toBeInTheDocument()
  })

  it("shows an empty state when there are no CVs", async () => {
    server.use(
      http.get(`${BASE}/cvs`, () => HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 })),
      http.get(`${BASE}/matches`, () => HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }))
    )

    renderPage()

    expect(
      await screen.findByText("You haven't uploaded a CV yet — upload one to get started.")
    ).toBeInTheDocument()
  })

  it("shows an error message when the request fails", async () => {
    server.use(
      http.get(`${BASE}/cvs`, () => HttpResponse.json({}, { status: 500 })),
      http.get(`${BASE}/matches`, () => HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }))
    )

    renderPage()

    expect(
      await screen.findByText("Couldn't load this list. Please try again.")
    ).toBeInTheDocument()
  })
})
