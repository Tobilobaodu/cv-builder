"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuthStore } from "@/store/auth-store"

/**
 * Gates authenticated routes. Waits for the Zustand `persist` middleware to
 * rehydrate from localStorage before deciding to redirect — otherwise every
 * dashboard page would bounce to /login for a logged-in user on first paint,
 * since the server-rendered default state has no token yet.
 *
 * It drives that wait with an explicit `rehydrate()` instead of trusting
 * `hasHydrated()`. Next's dev SSR pass evaluates the store module in Node,
 * and Node now exposes a *global* `localStorage` (v22+; `node -e "typeof
 * localStorage"` reports "object" here — the same quirk package.json's test
 * script suppresses with --no-experimental-webstorage). `hasHydrated()` can
 * therefore report true off the back of that empty server-side storage while
 * the in-memory state still holds no token, and redirecting on that stale
 * read is what made a plain tab refresh look like a logout even though the
 * browser's localStorage still had a perfectly good session in it.
 *
 * Starting `hasHydrated` at false also keeps the first client render
 * identical to the server's ("Loading…"), so the gate can't produce a
 * hydration mismatch on the way through.
 */
export function useRequireAuth() {
  const router = useRouter()
  const accessToken = useAuthStore((state) => state.accessToken)
  const [hasHydrated, setHasHydrated] = useState(false)

  useEffect(() => {
    let active = true
    const markHydrated = () => {
      if (active) setHasHydrated(true)
    }

    // Re-reads the browser's own storage and applies it to the store before
    // anything is allowed to act on "there is no token". Resolves
    // immediately when the store was already hydrated correctly, so this
    // costs nothing in the normal case.
    Promise.resolve(useAuthStore.persist.rehydrate()).then(
      markHydrated,
      markHydrated
    )
    const unsubscribe = useAuthStore.persist.onFinishHydration(markHydrated)

    return () => {
      active = false
      unsubscribe()
    }
  }, [])

  useEffect(() => {
    if (hasHydrated && !accessToken) {
      router.replace("/login")
    }
  }, [hasHydrated, accessToken, router])

  return { isReady: hasHydrated && !!accessToken }
}
