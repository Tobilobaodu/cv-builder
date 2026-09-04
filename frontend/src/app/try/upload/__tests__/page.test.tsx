import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import TailorPage from "@/app/try/upload/page"

const toastError = vi.fn()
vi.mock("sonner", () => ({
  toast: { error: (...args: unknown[]) => toastError(...args) },
}))

const BASE = "http://localhost:8000/api/v1"
const EXTRACTED = "TOBILOBA ODU\nProduct Designer\n\nEarlier Career\nKarrox"

function pdfFile(name = "resume.pdf") {
  return new File([new Uint8Array(1024)], name, { type: "application/pdf" })
}

function trialSessionHandler(status = 201) {
  return http.post(`${BASE}/trial-sessions`, () =>
    status === 201
      ? HttpResponse.json(
          {
            trialSessionId: "trial-1",
            expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
          },
          { status: 201 }
        )
      : HttpResponse.json({ detail: "Too many requests." }, { status })
  )
}

function uploadHandler(status = 202) {
  return http.post(`${BASE}/cvs`, () =>
    status === 202
      ? HttpResponse.json(
          { cvId: "cv-1", processingJobId: "job-1" },
          { status: 202 }
        )
      : HttpResponse.json({ detail: "Too many requests." }, { status })
  )
}

const rawTextHandler = http.get(`${BASE}/cvs/cv-1/raw-text`, () =>
  HttpResponse.json({
    cvId: "cv-1",
    canonicalText: EXTRACTED,
    characters: EXTRACTED.length,
    mergeStrategy: null,
    ocrUsed: false,
  })
)

const JOB_TEXT =
  "Senior Product Designer. Requirements: Figma, design systems, UX research, " +
  "usability testing, WCAG 2.1 accessibility."

function jobPostUrlHandler(status = 202) {
  return http.post(`${BASE}/job-posts/url`, () =>
    status === 202
      ? HttpResponse.json(
          { jobPostId: "jp-1", processingJobId: "job-2" },
          { status: 202 }
        )
      : HttpResponse.json({ detail: "Too many requests." }, { status })
  )
}

function jobPostHandler(body: Record<string, unknown>) {
  return http.get(`${BASE}/job-posts/jp-1`, () =>
    HttpResponse.json({
      id: "jp-1",
      sourceType: "url",
      sourceUrl: "https://example.com/careers/role",
      rawText: "",
      status: "pending",
      errorMessage: null,
      profile: null,
      ...body,
    })
  )
}

// S1: analysis (score/gaps/tips) is now its own fast JSON call.
function matchAnalysisHandler() {
  return http.post(`${BASE}/match-analyses`, () =>
    HttpResponse.json({
      matchNotes: ["Design systems evidenced at iSixty."],
      informationNeeded: ["Which WCAG level was audited?"],
      stats: {
        cvOccupation: "Product Designer",
        jobOccupation: "Product Designer",
        sameOccupation: true,
        atsScore: 85,
        matchLabel: "Strong match",
        matchedSkills: ["Figma", "Design systems"],
        transferableSkills: ["Workshop facilitation"],
        missingSkills: [],
        priorityKeywords: ["WCAG 2.1"],
        literalCoverage: { coverage: 1, present: ["WCAG 2.1"], absent: [] },
      },
      promptVersion: "resume-analysis-v1",
    })
  )
}

// S2: the tailored CV is now a streamed SSE response — matches
// apiFetchStream's framing (`data: {...}\n\n`) and resume_rewrite.py's
// RewriteStreamEvent shape.
function sseFrame(type: string, fields: Record<string, unknown> = {}) {
  return `data: ${JSON.stringify({ type, ...fields })}\n\n`
}

function rewriteStreamHandler() {
  return http.post(`${BASE}/resume-rewrites`, () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(sseFrame("delta", { text: "## Professional Summary\n" }))
        )
        controller.enqueue(
          encoder.encode(sseFrame("delta", { text: "Product designer." }))
        )
        controller.enqueue(
          encoder.encode(sseFrame("done", { text: "", informationNeeded: [] }))
        )
        controller.close()
      },
    })
    return new HttpResponse(body, {
      headers: { "Content-Type": "text/event-stream" },
    })
  })
}

describe("TailorPage (/try/upload)", () => {
  it("starts uploading as soon as a file is chosen, with no submit", async () => {
    server.use(trialSessionHandler(), uploadHandler(), rawTextHandler)
    const user = userEvent.setup()
    render(<TailorPage />)

    // The page deliberately has no upload button — selecting the file is
    // the whole interaction.
    expect(screen.queryByRole("button", { name: /upload/i })).toBeNull()

    await user.upload(screen.getByTestId("input-cv-file"), pdfFile())

    await waitFor(() =>
      expect(screen.getByTestId("status-ready")).toBeInTheDocument()
    , { timeout: 8000 })
  }, 15000)

  it("opens the extracted-CV panel by itself and shows the real text", async () => {
    server.use(trialSessionHandler(), uploadHandler(), rawTextHandler)
    const user = userEvent.setup()
    render(<TailorPage />)

    await user.upload(screen.getByTestId("input-cv-file"), pdfFile())

    await waitFor(() =>
      expect(screen.getByTestId("modal-extracted-cv")).toBeInTheDocument()
    , { timeout: 8000 })
    expect(screen.getByTestId("text-extracted-cv")).toHaveTextContent("TOBILOBA ODU")
    // The block the retired structured parser folded into the previous role.
    expect(screen.getByTestId("text-extracted-cv")).toHaveTextContent("Earlier Career")

    await user.click(screen.getByTestId("button-close-extracted"))
    expect(screen.queryByTestId("modal-extracted-cv")).toBeNull()

    await user.click(screen.getByTestId("button-view-extracted"))
    expect(screen.getByTestId("modal-extracted-cv")).toBeInTheDocument()
  }, 15000)

  it("blames the hourly limit, not the file, when the trial session is rate-limited", async () => {
    server.use(trialSessionHandler(429), uploadHandler(), rawTextHandler)
    const user = userEvent.setup()
    render(<TailorPage />)

    await user.upload(screen.getByTestId("input-cv-file"), pdfFile())

    const failure = await screen.findByTestId("status-upload-failed", {}, { timeout: 8000 })
    expect(failure).toHaveTextContent(/free trial sessions for the hour/)
    expect(failure).not.toHaveTextContent(/couldn't upload that file/)
  }, 15000)

  it("blames the hourly limit when the upload itself is rate-limited", async () => {
    server.use(trialSessionHandler(), uploadHandler(429), rawTextHandler)
    const user = userEvent.setup()
    render(<TailorPage />)

    await user.upload(screen.getByTestId("input-cv-file"), pdfFile())

    const failure = await screen.findByTestId("status-upload-failed", {}, { timeout: 8000 })
    expect(failure).toHaveTextContent(/Upload limit reached for the hour/)
  }, 15000)
  // ── job post from a URL ─────────────────────────────────────────────
  // There is no separate fetch button: on the URL tab, "Tailor my CV"
  // fetches the posting and then analyses it in one click.

  async function uploadAndWaitReady(user: ReturnType<typeof userEvent.setup>) {
    await user.upload(screen.getByTestId("input-cv-file"), pdfFile())
    await waitFor(
      () => expect(screen.getByTestId("status-ready")).toBeInTheDocument(),
      { timeout: 8000 }
    )
  }

  it("fetches the URL and analyses it from the one button", async () => {
    server.use(
      trialSessionHandler(),
      uploadHandler(),
      rawTextHandler,
      jobPostUrlHandler(),
      jobPostHandler({ rawText: JOB_TEXT, status: "structuring" }),
      matchAnalysisHandler(),
      rewriteStreamHandler()
    )
    const user = userEvent.setup()
    render(<TailorPage />)
    await uploadAndWaitReady(user)

    await user.click(screen.getByTestId("tab-job-url"))
    await user.type(
      screen.getByTestId("input-job-url"),
      "https://example.com/careers/role"
    )
    expect(screen.queryByTestId("button-fetch-job-url")).toBeNull()

    await user.click(screen.getByTestId("button-analyse"))

    await waitFor(
      () => expect(screen.getByTestId("state-complete")).toBeInTheDocument(),
      { timeout: 10000 }
    )
    // The fetched text is left visible and editable, on the paste tab.
    expect(screen.getByTestId("input-job-description")).toHaveValue(JOB_TEXT)
    expect(screen.getByTestId("status-job-fetched")).toBeInTheDocument()

    // S2: the tailored CV streams in as a separate, later step — the score
    // above doesn't wait for it.
    await waitFor(
      () =>
        expect(screen.getByTestId("text-tailored-cv")).toHaveTextContent(
          "Product designer."
        ),
      { timeout: 10000 }
    )
  }, 20000)

  it("shows the backend's own reason when a URL is refused, and does not analyse", async () => {
    const reason =
      "URL rejected for security reasons. Private address. " +
      "Please paste the job description text directly instead."
    let rewriteCalls = 0
    server.use(
      trialSessionHandler(),
      uploadHandler(),
      rawTextHandler,
      jobPostUrlHandler(),
      jobPostHandler({ status: "failed", errorMessage: reason }),
      http.post(`${BASE}/resume-rewrites`, () => {
        rewriteCalls += 1
        return HttpResponse.json({ detail: "should not be called" }, { status: 500 })
      })
    )
    const user = userEvent.setup()
    render(<TailorPage />)
    await uploadAndWaitReady(user)

    await user.click(screen.getByTestId("tab-job-url"))
    await user.type(screen.getByTestId("input-job-url"), "http://127.0.0.1/admin")
    await user.click(screen.getByTestId("button-analyse"))

    const failure = await screen.findByTestId(
      "status-job-fetch-failed", {}, { timeout: 10000 }
    )
    expect(failure).toHaveTextContent(/rejected for security reasons/)
    expect(rewriteCalls).toBe(0)
    expect(screen.queryByTestId("state-complete")).toBeNull()
  }, 20000)

  it("blames the hourly limit when the URL submission is rate-limited", async () => {
    server.use(
      trialSessionHandler(),
      uploadHandler(),
      rawTextHandler,
      jobPostUrlHandler(429)
    )
    const user = userEvent.setup()
    render(<TailorPage />)
    await uploadAndWaitReady(user)

    await user.click(screen.getByTestId("tab-job-url"))
    await user.type(screen.getByTestId("input-job-url"), "https://example.com/x")
    await user.click(screen.getByTestId("button-analyse"))

    const failure = await screen.findByTestId(
      "status-job-fetch-failed", {}, { timeout: 10000 }
    )
    expect(failure).toHaveTextContent(/URL-fetch limit reached for the hour/)
  }, 20000)
})
