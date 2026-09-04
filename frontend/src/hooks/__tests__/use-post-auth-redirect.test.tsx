import { describe, expect, it, vi } from "vitest"
import { renderHook } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import { usePostAuthRedirect } from "@/hooks/use-post-auth-redirect"
import { useTrialStore } from "@/store/trial-store"
import { useAuthStore } from "@/store/auth-store"

const push = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}))

const toastError = vi.fn()
vi.mock("sonner", () => ({
  toast: { error: (...args: unknown[]) => toastError(...args) },
}))

const BASE = "http://localhost:8000/api/v1"

describe("usePostAuthRedirect", () => {
  it("goes straight to /dashboard when there is no active trial session", async () => {
    const { result } = renderHook(() => usePostAuthRedirect())

    await result.current()

    expect(push).toHaveBeenCalledWith("/dashboard")
  })

  it("claims the trial session, marks it claimed, and goes to /dashboard/continue on success", async () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })
    useTrialStore.setState({
      trialSessionId: "trial-1",
      expiresAt: new Date().toISOString(),
      cvId: "cv-1",
      matchId: "match-1",
    })

    let receivedAuth: string | null = null
    let receivedBody: unknown = null
    server.use(
      http.post(`${BASE}/auth/claim-trial`, async ({ request }) => {
        receivedAuth = request.headers.get("Authorization")
        receivedBody = await request.json()
        return HttpResponse.json({
          claimed: true,
          cvFilesReassigned: 1,
          jobPostsReassigned: 1,
          matchRunsReassigned: 1,
        })
      })
    )

    const { result } = renderHook(() => usePostAuthRedirect())
    await result.current()

    expect(receivedAuth).toBe("Bearer token-1")
    expect(receivedBody).toEqual({ trialSessionId: "trial-1" })
    expect(push).toHaveBeenCalledWith("/dashboard/continue")
    expect(useTrialStore.getState().trialSessionId).toBeNull()
    // Workflow ids survive the claim — the continuation screen reads them.
    expect(useTrialStore.getState().cvId).toBe("cv-1")
    expect(useTrialStore.getState().matchId).toBe("match-1")
  })

  it("shows an error and still goes to /dashboard when the claim is rejected (already claimed/expired)", async () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })
    useTrialStore.setState({
      trialSessionId: "trial-1",
      expiresAt: new Date().toISOString(),
    })

    server.use(
      http.post(`${BASE}/auth/claim-trial`, () =>
        HttpResponse.json({ detail: "Trial session already claimed." }, { status: 409 })
      )
    )

    const { result } = renderHook(() => usePostAuthRedirect())
    await result.current()

    expect(toastError).toHaveBeenCalledWith("Trial session already claimed.")
    expect(push).toHaveBeenCalledWith("/dashboard")
  })
})
