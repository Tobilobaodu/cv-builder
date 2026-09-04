import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import LoginPage from "@/app/login/page"
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

describe("LoginPage", () => {
  it("shows validation errors and does not submit when fields are empty", async () => {
    const user = userEvent.setup()
    render(<LoginPage />)

    await user.click(screen.getByRole("button", { name: "Log in" }))

    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument()
    expect(push).not.toHaveBeenCalled()
  })

  it("logs in successfully and redirects to /dashboard", async () => {
    const user = userEvent.setup()
    render(<LoginPage />)

    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), "password123")
    await user.click(screen.getByRole("button", { name: "Log in" }))

    await waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard"))
    expect(useAuthStore.getState().accessToken).toBe("test-access-token")
  })

  it("shows an error toast on invalid credentials (401)", async () => {
    server.use(
      http.post(`${BASE}/auth/login`, () =>
        HttpResponse.json({ detail: "Invalid email or password." }, { status: 401 })
      )
    )
    const user = userEvent.setup()
    render(<LoginPage />)

    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), "wrongpass")
    await user.click(screen.getByRole("button", { name: "Log in" }))

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Invalid email or password.")
    )
    expect(push).not.toHaveBeenCalled()
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
    render(<LoginPage />)

    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), "password123")
    await user.click(screen.getByRole("button", { name: "Log in" }))

    await waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard/continue"))
    expect(useTrialStore.getState().trialSessionId).toBeNull()
  })

  it("shows a rate-limit toast on 429", async () => {
    server.use(
      http.post(`${BASE}/auth/login`, () =>
        HttpResponse.json(
          { detail: "Too many login attempts. Please wait and try again." },
          { status: 429 }
        )
      )
    )
    const user = userEvent.setup()
    render(<LoginPage />)

    await user.type(screen.getByLabelText("Email"), "a@b.com")
    await user.type(screen.getByLabelText("Password"), "password123")
    await user.click(screen.getByRole("button", { name: "Log in" }))

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        "Too many login attempts. Please wait and try again."
      )
    )
  })
})
