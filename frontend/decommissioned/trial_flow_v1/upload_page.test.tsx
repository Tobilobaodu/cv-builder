import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import TrialUploadPage from "@/app/try/upload/page"
import { useTrialStore } from "@/store/trial-store"

const push = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}))

const toastError = vi.fn()
vi.mock("sonner", () => ({
  toast: { error: (...args: unknown[]) => toastError(...args) },
}))

const BASE = "http://localhost:8000/api/v1"

function pdfFile(name = "resume.pdf") {
  return new File([new Uint8Array(1024)], name, { type: "application/pdf" })
}

describe("TrialUploadPage", () => {
  it("shows a validation error when submitting without a CV file", async () => {
    const user = userEvent.setup()
    render(<TrialUploadPage />)

    await user.type(screen.getByLabelText("Job description"), "x".repeat(150))
    await user.click(screen.getByRole("button", { name: "Run my match" }))

    expect(await screen.findByText("Choose a CV file.")).toBeInTheDocument()
    expect(push).not.toHaveBeenCalled()
  })

  it("shows a validation error when pasted job text is too short", async () => {
    const user = userEvent.setup()
    render(<TrialUploadPage />)

    const fileInput = document.getElementById("cv-file") as HTMLInputElement
    await user.upload(fileInput, pdfFile())
    await user.type(screen.getByLabelText("Job description"), "too short")
    await user.click(screen.getByRole("button", { name: "Run my match" }))

    expect(
      await screen.findByText("Paste at least 100 characters of the job description.")
    ).toBeInTheDocument()
    expect(push).not.toHaveBeenCalled()
  })

  it("uploads the CV, submits the job text, stores workflow ids, and navigates to /try/results", async () => {
    server.use(
      http.post(`${BASE}/cvs`, () =>
        HttpResponse.json(
          {
            cvId: "cv-1",
            processingJobId: "job-cv-1",
            status: "queued",
            filename: "resume.pdf",
            fileSize: 1024,
            mimeType: "application/pdf",
          },
          { status: 202 }
        )
      ),
      http.post(`${BASE}/job-posts/text`, () =>
        HttpResponse.json(
          { jobPostId: "jp-1", processingJobId: "job-jp-1" },
          { status: 202 }
        )
      )
    )

    const user = userEvent.setup()
    render(<TrialUploadPage />)

    const fileInput = document.getElementById("cv-file") as HTMLInputElement
    await user.upload(fileInput, pdfFile())
    await user.type(screen.getByLabelText("Job description"), "x".repeat(150))
    await user.click(screen.getByRole("button", { name: "Run my match" }))

    await waitFor(() => expect(push).toHaveBeenCalledWith("/try/results"))

    const state = useTrialStore.getState()
    expect(state.cvId).toBe("cv-1")
    expect(state.cvProcessingJobId).toBe("job-cv-1")
    expect(state.jobPostId).toBe("jp-1")
    expect(state.jobPostProcessingJobId).toBe("job-jp-1")
  })

  it("shows an error toast and does not navigate when the upload fails", async () => {
    server.use(
      http.post(`${BASE}/cvs`, () =>
        HttpResponse.json({ detail: "Uploaded file failed security scan." }, { status: 400 })
      )
    )

    const user = userEvent.setup()
    render(<TrialUploadPage />)

    const fileInput = document.getElementById("cv-file") as HTMLInputElement
    await user.upload(fileInput, pdfFile())
    await user.type(screen.getByLabelText("Job description"), "x".repeat(150))
    await user.click(screen.getByRole("button", { name: "Run my match" }))

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Uploaded file failed security scan.")
    )
    expect(push).not.toHaveBeenCalled()
  })
})
