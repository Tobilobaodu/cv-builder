import { describe, expect, it, vi } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { useRequireAuth } from "@/hooks/use-require-auth"
import { useAuthStore } from "@/store/auth-store"

const replace = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}))

describe("useRequireAuth", () => {
  it("redirects to /login when there is no access token", async () => {
    const { result } = renderHook(() => useRequireAuth())

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"))
    expect(result.current.isReady).toBe(false)
  })

  it("does not redirect and reports ready when logged in", async () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })

    const { result } = renderHook(() => useRequireAuth())

    await waitFor(() => expect(result.current.isReady).toBe(true))
    expect(replace).not.toHaveBeenCalled()
  })
})
