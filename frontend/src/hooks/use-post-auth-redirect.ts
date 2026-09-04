import { useRouter } from "next/navigation"
import { toast } from "sonner"
import { claimTrialSession } from "@/lib/trial-api"
import { errorMessage, ApiError } from "@/lib/api"
import { useTrialStore } from "@/store/trial-store"

/**
 * Shared by /login and /register: if a trial session is active, claim it
 * (reassigns the anonymous CV/match/draft to the new account) and land on
 * the continuation screen; otherwise go straight to the dashboard as
 * before. Claiming is a one-time transition (backend 409s a repeat), so a
 * failure here doesn't block the user from continuing — it's surfaced but
 * non-fatal.
 */
export function usePostAuthRedirect() {
  const router = useRouter()
  const trialSessionId = useTrialStore((s) => s.trialSessionId)
  const markTrialSessionClaimed = useTrialStore((s) => s.markTrialSessionClaimed)

  return async function redirectAfterAuth() {
    if (!trialSessionId) {
      router.push("/dashboard")
      return
    }

    try {
      await claimTrialSession(trialSessionId)
      markTrialSessionClaimed()
      router.push("/dashboard/continue")
    } catch (error) {
      if (error instanceof ApiError && (error.status === 404 || error.status === 409)) {
        toast.error(
          errorMessage(error, "Your trial session couldn't be attached to this account.")
        )
      } else {
        toast.error(errorMessage(error, "Couldn't restore your trial progress."))
      }
      router.push("/dashboard")
    }
  }
}
