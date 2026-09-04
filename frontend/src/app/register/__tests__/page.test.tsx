import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import RegisterPage from "@/app/register/page"
import { useAuthStore } from "@/store/auth-store"
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

// 12 characters — the backend's RegisterRequest.password min_length. Every
// password here must satisfy it, or these tests would be asserting a flow
// the real API rejects with a 422.
const VALID_PASSWORD = "password1234"

describe("RegisterPage", () => {
  it("rejects a password shorter than 12 characters", async () => {
    const user = userEvent.setup()
    render(<RegisterPage />)

    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), "short")
    await user.type(screen.getByLabelText("Confirm password"), "short")
    await user.click(screen.getByRole("button", { name: "Create account" }))

    expect(
      await screen.findByText("Password must be at least 12 characters.")
    ).toBeInTheDocument()
    expect(push).not.toHaveBeenCalled()
  })

  it("rejects an 11-character password the backend would 422 on", async () => {
    const user = userEvent.setup()
    render(<RegisterPage />)

    // Exactly the case that was silently broken: 11 chars passed the old
    // min(8) client rule, then failed server-side with an unsurfaced 422.
    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), "password123")
    await user.type(screen.getByLabelText("Confirm password"), "password123")
    await user.click(screen.getByRole("button", { name: "Create account" }))

    expect(
      await screen.findByText("Password must be at least 12 characters.")
    ).toBeInTheDocument()
    expect(push).not.toHaveBeenCalled()
  })

  it("rejects mismatched passwords", async () => {
    const user = userEvent.setup()
    render(<RegisterPage />)

    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), VALID_PASSWORD)
    await user.type(screen.getByLabelText("Confirm password"), "password1235")
    await user.click(screen.getByRole("button", { name: "Create account" }))

    expect(await screen.findByText("Passwords do not match.")).toBeInTheDocument()
    expect(push).not.toHaveBeenCalled()
  })

  it("registers, logs in, and redirects to /dashboard", async () => {
    const user = userEvent.setup()
    render(<RegisterPage />)

    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), VALID_PASSWORD)
    await user.type(screen.getByLabelText("Confirm password"), VALID_PASSWORD)
    await user.click(screen.getByRole("button", { name: "Create account" }))

    await waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard"))
    expect(useAuthStore.getState().accessToken).toBe("test-access-token")
  })

  it("claims an active trial session and redirects to /dashboard/continue instead of /dashboard", async () => {
    useTrialStore.setState({
      trialSessionId: "trial-1",
      expiresAt: new Date().toISOString(),
    })
    server.use(
      http.post(`${BASE}/auth/claim-trial`, () =>
        HttpResponse.json({
          claimed: true,
          cvFilesReassigned: 1,
          jobPostsReassigned: 1,
          matchRunsReassigned: 1,
        })
      )
    )

    const user = userEvent.setup()
    render(<RegisterPage />)

    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), VALID_PASSWORD)
    await user.type(screen.getByLabelText("Confirm password"), VALID_PASSWORD)
    await user.click(screen.getByRole("button", { name: "Create account" }))

    await waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard/continue"))
    expect(useTrialStore.getState().trialSessionId).toBeNull()
  })

  it("shows an error toast when the email is already registered (409)", async () => {
    server.use(
      http.post(`${BASE}/auth/register`, () =>
        HttpResponse.json(
          { detail: "An account with this email already exists." },
          { status: 409 }
        )
      )
    )
    const user = userEvent.setup()
    render(<RegisterPage />)

    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), VALID_PASSWORD)
    await user.type(screen.getByLabelText("Confirm password"), VALID_PASSWORD)
    await user.click(screen.getByRole("button", { name: "Create account" }))

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        "An account with this email already exists."
      )
    )
    expect(push).not.toHaveBeenCalled()
  })

  it("surfaces the reason from a backend 422 instead of a generic message", async () => {
    // Regression guard: a 422's `detail` is an array, not a string, so this
    // used to fall through to "Could not create your account." and hide the
    // actual cause. Any future frontend/backend validation drift must stay
    // visible to the user.
    server.use(
      http.post(`${BASE}/auth/register`, () =>
        HttpResponse.json(
          {
            detail: [
              {
                type: "string_too_short",
                loc: ["body", "password"],
                msg: "String should have at least 12 characters",
                ctx: { min_length: 12 },
              },
            ],
          },
          { status: 422 }
        )
      )
    )

    const user = userEvent.setup()
    render(<RegisterPage />)

    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), VALID_PASSWORD)
    await user.type(screen.getByLabelText("Confirm password"), VALID_PASSWORD)
    await user.click(screen.getByRole("button", { name: "Create account" }))

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        "password: String should have at least 12 characters"
      )
    )
    expect(push).not.toHaveBeenCalled()
  })
})
