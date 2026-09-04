"use client"

import { useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { toast } from "sonner"
import { createTrialSession } from "@/lib/trial-api"
import { useTrialStore } from "@/store/trial-store"

export default function TryPage() {
  const router = useRouter()
  const trialSessionId = useTrialStore((s) => s.trialSessionId)
  const expiresAt = useTrialStore((s) => s.expiresAt)
  const setTrialSession = useTrialStore((s) => s.setTrialSession)
  const startedRef = useRef(false)

  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true

    const hasValidSession =
      trialSessionId && expiresAt && new Date(expiresAt) > new Date()

    if (hasValidSession) {
      router.replace("/try/upload")
      return
    }

    createTrialSession()
      .then((result) => {
        setTrialSession(result.trialSessionId, result.expiresAt)
        router.replace("/try/upload")
      })
      .catch(() => {
        toast.error("Couldn't start your trial. Please try again.")
      })
  }, [trialSessionId, expiresAt, router, setTrialSession])

  return (
    <div style={{ maxWidth: 400, margin: "0 auto", padding: "96px 24px", textAlign: "center", color: "var(--color-neutral-700)" }}>
      Setting up your trial…
    </div>
  )
}
