import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import { createQueryWrapper } from "@/test/query-wrapper"
import TrialResultsPage from "@/app/try/results/page"
import { useTrialStore } from "@/store/trial-store"
import { useAuthStore } from "@/store/auth-store"

const push = vi.fn()
const replace = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
}))

vi.mock("sonner", () => ({
  toast: { error: vi.fn() },
}))

const BASE = "http://localhost:8000/api/v1"

function jobResponse(id: string, sourceEntityId: string, status = "completed") {
  return {
    id,
    jobType: "generic",
    sourceEntityType: "x",
    sourceEntityId,
    status,
    retryCount: 0,
    lastError: null,
    createdAt: new Date().toISOString(),
    completedAt: new Date().toISOString(),
  }
}

function renderResultsPage() {
  const Wrapper = createQueryWrapper()
  return render(
    <Wrapper>
      <TrialResultsPage />
    </Wrapper>
  )
}

describe("TrialResultsPage", () => {
  beforeEach(() => {
    useTrialStore.setState({
      cvId: "cv-1",
      cvProcessingJobId: "job-cv-1",
      jobPostId: "jp-1",
      jobPostProcessingJobId: "job-jp-1",
      cvProfileVersionId: null,
      matchId: null,
      draftId: null,
    })
  })

  it("walks the full chain: parse -> match -> tailored CV, and renders both", async () => {
    let draftStatus: string = "generated"
    server.use(
      http.get(`${BASE}/jobs/job-cv-1`, () => HttpResponse.json(jobResponse("job-cv-1", "cv-1"))),
      http.get(`${BASE}/jobs/job-jp-1`, () => HttpResponse.json(jobResponse("job-jp-1", "jp-1"))),
      http.get(`${BASE}/cvs/cv-1/parsed-profile`, () =>
        HttpResponse.json({
          cvId: "cv-1",
          profileVersionId: "pv-1",
          versionNumber: 1,
          structuredPayload: { basics: { summary: "Experienced engineer." } },
        })
      ),
      http.get(`${BASE}/job-posts/jp-1`, () =>
        HttpResponse.json({
          id: "jp-1",
          sourceType: "text",
          sourceUrl: null,
          status: "completed",
          errorMessage: null,
          profile: { jobTitle: "Senior Engineer", employer: "Acme" },
        })
      ),
      http.post(`${BASE}/matches`, () =>
        HttpResponse.json(
          { matchId: "match-1", processingJobId: "job-match-1" },
          { status: 202 }
        )
      ),
      http.get(`${BASE}/jobs/job-match-1`, () =>
        HttpResponse.json(jobResponse("job-match-1", "match-1"))
      ),
      http.get(`${BASE}/matches/match-1`, () =>
        HttpResponse.json({
          id: "match-1",
          status: "completed",
          score: 82,
          supportedCount: 5,
          partialCount: 2,
          unsupportedCount: 1,
          totalRequirements: 8,
          summaryAnalysis: "Strong fit for this role.",
        })
      ),
      http.post(`${BASE}/matches/match-1/tailored-cv`, () =>
        HttpResponse.json({ jobId: "job-draft-1", status: "queued" }, { status: 202 })
      ),
      http.get(`${BASE}/jobs/job-draft-1`, () =>
        HttpResponse.json(jobResponse("job-draft-1", "draft-1"))
      ),
      http.get(`${BASE}/tailored-cvs/draft-1`, () =>
        HttpResponse.json({
          id: "draft-1",
          matchRunId: "match-1",
          versionNumber: 1,
          status: draftStatus,
          sections: [
            {
              id: "sec-1",
              sectionType: "summary",
              contentText: "Tailored summary text.",
              orderIndex: 0,
            },
          ],
          improvementChecklist: null,
        })
      ),
      http.post(`${BASE}/tailored-cvs/draft-1/approve`, () => {
        draftStatus = "approved"
        return HttpResponse.json({
          id: "draft-1",
          matchRunId: "match-1",
          versionNumber: 1,
          status: "approved",
          sections: [],
          improvementChecklist: null,
        })
      })
    )

    renderResultsPage()

    expect(await screen.findByText("Match score: 82")).toBeInTheDocument()
    // Exact match — "Generating your tailored CV…" (the loading state) is
    // also a substring match for "Your tailored CV" and would falsely pass
    // a non-exact query regardless of whether generation actually finished
    // (this exact confusion cost real debugging time in the e2e version of
    // this flow — see e2e/trial-flow.spec.ts).
    expect(await screen.findByText("Your tailored CV", { exact: true })).toBeInTheDocument()
    expect(screen.getByText("Tailored summary text.")).toBeInTheDocument()
    expect(screen.getByText("Strong fit for this role.")).toBeInTheDocument()

    // Auto-approved (no manual review step in the trial flow), which is
    // required before the download endpoint will accept the export.
    expect(
      await screen.findByRole("button", { name: "Download trial CV" })
    ).toBeEnabled()

    expect(useTrialStore.getState().cvProfileVersionId).toBe("pv-1")
    expect(useTrialStore.getState().matchId).toBe("match-1")
    expect(useTrialStore.getState().draftId).toBe("draft-1")

    // Unauthenticated (the default in this test) — clicking "Create a cover
    // letter" opens the paywall rather than doing anything else.
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Create a cover letter for this job" }))
    expect(await screen.findByText("Create your account to continue")).toBeInTheDocument()
  })

  it("shows a disabled 'Coming soon' cover-letter button instead of the paywall when authenticated", async () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })
    server.use(
      http.get(`${BASE}/jobs/job-cv-1`, () => HttpResponse.json(jobResponse("job-cv-1", "cv-1"))),
      http.get(`${BASE}/jobs/job-jp-1`, () => HttpResponse.json(jobResponse("job-jp-1", "jp-1"))),
      http.get(`${BASE}/cvs/cv-1/parsed-profile`, () =>
        HttpResponse.json({
          cvId: "cv-1",
          profileVersionId: "pv-1",
          versionNumber: 1,
          structuredPayload: {},
        })
      ),
      http.get(`${BASE}/job-posts/jp-1`, () =>
        HttpResponse.json({
          id: "jp-1",
          sourceType: "text",
          sourceUrl: null,
          status: "completed",
          errorMessage: null,
          profile: { jobTitle: "Senior Engineer", employer: "Acme" },
        })
      ),
      http.post(`${BASE}/matches`, () =>
        HttpResponse.json(
          { matchId: "match-1", processingJobId: "job-match-1" },
          { status: 202 }
        )
      ),
      http.get(`${BASE}/jobs/job-match-1`, () =>
        HttpResponse.json(jobResponse("job-match-1", "match-1"))
      ),
      http.get(`${BASE}/matches/match-1`, () =>
        HttpResponse.json({
          id: "match-1",
          status: "completed",
          score: 82,
          supportedCount: 5,
          partialCount: 2,
          unsupportedCount: 1,
          totalRequirements: 8,
          summaryAnalysis: "Strong fit for this role.",
        })
      ),
      http.post(`${BASE}/matches/match-1/tailored-cv`, () =>
        HttpResponse.json({ jobId: "job-draft-1", status: "queued" }, { status: 202 })
      ),
      http.get(`${BASE}/jobs/job-draft-1`, () =>
        HttpResponse.json(jobResponse("job-draft-1", "draft-1"))
      ),
      http.get(`${BASE}/tailored-cvs/draft-1`, () =>
        HttpResponse.json({
          id: "draft-1",
          matchRunId: "match-1",
          versionNumber: 1,
          status: "approved",
          sections: [
            { id: "sec-1", sectionType: "summary", contentText: "Text.", orderIndex: 0 },
          ],
          improvementChecklist: null,
        })
      )
    )

    renderResultsPage()

    const button = await screen.findByRole("button", {
      name: "Create a cover letter for this job",
    })
    expect(button).toBeDisabled()

    const user = userEvent.setup()
    await user.click(button)
    expect(screen.queryByText("Create your account to continue")).not.toBeInTheDocument()
  })

  it("shows an honest failure message when generation produces no evidence-backed sections", async () => {
    server.use(
      http.get(`${BASE}/jobs/job-cv-1`, () => HttpResponse.json(jobResponse("job-cv-1", "cv-1"))),
      http.get(`${BASE}/jobs/job-jp-1`, () => HttpResponse.json(jobResponse("job-jp-1", "jp-1"))),
      http.get(`${BASE}/cvs/cv-1/parsed-profile`, () =>
        HttpResponse.json({
          cvId: "cv-1",
          profileVersionId: "pv-1",
          versionNumber: 1,
          structuredPayload: {},
        })
      ),
      http.get(`${BASE}/job-posts/jp-1`, () =>
        HttpResponse.json({
          id: "jp-1",
          sourceType: "text",
          sourceUrl: null,
          status: "completed",
          errorMessage: null,
          profile: { jobTitle: "Senior Engineer", employer: "Acme" },
        })
      ),
      http.post(`${BASE}/matches`, () =>
        HttpResponse.json(
          { matchId: "match-1", processingJobId: "job-match-1" },
          { status: 202 }
        )
      ),
      http.get(`${BASE}/jobs/job-match-1`, () =>
        HttpResponse.json(jobResponse("job-match-1", "match-1"))
      ),
      http.get(`${BASE}/matches/match-1`, () =>
        HttpResponse.json({
          id: "match-1",
          status: "completed",
          score: 40,
          supportedCount: 0,
          partialCount: 0,
          unsupportedCount: 8,
          totalRequirements: 8,
          summaryAnalysis: null,
        })
      ),
      http.post(`${BASE}/matches/match-1/tailored-cv`, () =>
        HttpResponse.json({ jobId: "job-draft-1", status: "queued" }, { status: 202 })
      ),
      http.get(`${BASE}/jobs/job-draft-1`, () =>
        HttpResponse.json(jobResponse("job-draft-1", "draft-1"))
      ),
      http.get(`${BASE}/tailored-cvs/draft-1`, () =>
        HttpResponse.json({
          id: "draft-1",
          matchRunId: "match-1",
          versionNumber: 1,
          status: "failed",
          sections: [],
          improvementChecklist: null,
        })
      )
    )

    renderResultsPage()

    expect(
      await screen.findByText(/We couldn't find enough matching, verifiable experience/)
    ).toBeInTheDocument()
    expect(screen.queryByText("Download trial CV")).not.toBeInTheDocument()
  })

  it("shows a retry message when CV parsing fails", async () => {
    server.use(
      http.get(`${BASE}/jobs/job-cv-1`, () =>
        HttpResponse.json(jobResponse("job-cv-1", "cv-1", "failed"))
      ),
      http.get(`${BASE}/jobs/job-jp-1`, () => HttpResponse.json(jobResponse("job-jp-1", "jp-1")))
    )

    renderResultsPage()

    expect(
      await screen.findByText("We couldn't read your CV. Please try a different file.")
    ).toBeInTheDocument()
  })

  it("redirects to /try when there is no active trial workflow", async () => {
    useTrialStore.setState({
      cvId: null,
      cvProcessingJobId: null,
      jobPostId: null,
      jobPostProcessingJobId: null,
    })

    renderResultsPage()

    expect(replace).toHaveBeenCalledWith("/try")
  })
})
