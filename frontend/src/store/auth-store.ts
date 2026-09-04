import { create } from "zustand"
import { persist } from "zustand/middleware"

export type AuthUser = {
  id: string
  email: string
}

type AuthState = {
  accessToken: string | null
  /** Redeemed for a new access token by lib/api.ts when a request 401s.
   *  Persisted alongside the access token because its whole purpose is to
   *  survive the reload that outlives the access token — keeping it in
   *  memory only would leave a refreshed tab with nothing to redeem. */
  refreshToken: string | null
  user: AuthUser | null
  setAuth: (
    accessToken: string,
    user: AuthUser,
    refreshToken?: string | null
  ) => void
  /** Post-refresh token swap: replaces the credentials without touching
   *  `user`, so a silent renewal can't blank the signed-in identity the
   *  topbar/navbar render from. */
  setTokens: (accessToken: string, refreshToken: string | null) => void
  clearAuth: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      // refreshToken is optional so existing two-argument callers keep
      // working; passing nothing leaves the session unrenewable rather
      // than silently clearing a token that is already stored.
      setAuth: (accessToken, user, refreshToken) =>
        set((state) => ({
          accessToken,
          user,
          refreshToken: refreshToken ?? state.refreshToken,
        })),
      setTokens: (accessToken, refreshToken) => set({ accessToken, refreshToken }),
      clearAuth: () => set({ accessToken: null, refreshToken: null, user: null }),
    }),
    {
      name: "auth-storage",
    }
  )
)

export function isAuthenticated(): boolean {
  return useAuthStore.getState().accessToken !== null
}
