import { describe, expect, it } from "vitest"
import { useAuthStore, isAuthenticated } from "@/store/auth-store"

describe("auth store", () => {
  it("starts logged out", () => {
    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
    expect(isAuthenticated()).toBe(false)
  })

  it("setAuth stores the token and user", () => {
    useAuthStore
      .getState()
      .setAuth("token-1", { id: "u1", email: "a@b.com" })

    expect(useAuthStore.getState().accessToken).toBe("token-1")
    expect(useAuthStore.getState().user).toEqual({ id: "u1", email: "a@b.com" })
    expect(isAuthenticated()).toBe(true)
  })

  it("clearAuth resets to logged out", () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })
    useAuthStore.getState().clearAuth()

    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
    expect(isAuthenticated()).toBe(false)
  })

  it("persists to localStorage under the auth-storage key", () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })

    const raw = window.localStorage.getItem("auth-storage")
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw as string)
    expect(parsed.state.accessToken).toBe("token-1")
  })

  it("persists the refresh token so a reloaded tab can renew its session", () => {
    useAuthStore
      .getState()
      .setAuth("token-1", { id: "u1", email: "a@b.com" }, "refresh-1")

    const parsed = JSON.parse(window.localStorage.getItem("auth-storage") as string)
    expect(parsed.state.refreshToken).toBe("refresh-1")
    expect(useAuthStore.getState().refreshToken).toBe("refresh-1")
  })

  it("keeps an existing refresh token when setAuth omits one", () => {
    useAuthStore
      .getState()
      .setAuth("token-1", { id: "u1", email: "a@b.com" }, "refresh-1")
    useAuthStore.getState().setAuth("token-2", { id: "u1", email: "a@b.com" })

    expect(useAuthStore.getState().refreshToken).toBe("refresh-1")
  })

  it("setTokens swaps credentials without disturbing the signed-in user", () => {
    useAuthStore
      .getState()
      .setAuth("token-1", { id: "u1", email: "a@b.com" }, "refresh-1")
    useAuthStore.getState().setTokens("token-2", "refresh-2")

    expect(useAuthStore.getState().accessToken).toBe("token-2")
    expect(useAuthStore.getState().refreshToken).toBe("refresh-2")
    expect(useAuthStore.getState().user).toEqual({ id: "u1", email: "a@b.com" })
  })

  it("clearAuth also drops the refresh token", () => {
    useAuthStore
      .getState()
      .setAuth("token-1", { id: "u1", email: "a@b.com" }, "refresh-1")
    useAuthStore.getState().clearAuth()

    expect(useAuthStore.getState().refreshToken).toBeNull()
  })
})
