import { describe, expect, it, vi } from "vitest"
import { render, waitFor } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import TryPage from "@/app/try/page"
import { useTrialStore } from "@/store/trial-store"

const replace = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}))

vi.mock("sonner", () => ({
  toast: { error: vi.fn() },
}))

const BASE = "http://localhost:8000/api/v1"

describe("TryPage", () => {
  it("bootstraps a new trial session and redirects to /try/upload", async () => {
    server.use(
      http.post(`${BASE}/trial-sessions`, () =>
        HttpResponse.json(
          { trialSessionId: "trial-1", expiresAt: new Date(Date.now() + 3600_000).toISOString() },
          { status: 201 }
        )
      )
    )

    render(<TryPage />)

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/try/upload"))
    expect(useTrialStore.getState().trialSessionId).toBe("trial-1")
  })

  it("reuses an existing, unexpired trial session without calling the API again", async () => {
    const expiresAt = new Date(Date.now() + 3600_000).toISOString()
    useTrialStore.setState({ trialSessionId: "existing-trial", expiresAt })

    let called = false
    server.use(
      http.post(`${BASE}/trial-sessions`, () => {
        called = true
        return HttpResponse.json({ trialSessionId: "new", expiresAt }, { status: 201 })
      })
    )

    render(<TryPage />)

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/try/upload"))
    expect(called).toBe(false)
    expect(useTrialStore.getState().trialSessionId).toBe("existing-trial")
  })
})
