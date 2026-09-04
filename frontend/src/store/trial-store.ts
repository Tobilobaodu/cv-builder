import { create } from "zustand"
import { persist } from "zustand/middleware"

type TrialState = {
  trialSessionId: string | null
  expiresAt: string | null
  cvId: string | null
  cvProcessingJobId: string | null
  jobPostId: string | null
  jobPostProcessingJobId: string | null
  cvProfileVersionId: string | null
  matchId: string | null
  draftId: string | null
  setTrialSession: (trialSessionId: string, expiresAt: string) => void
  markTrialSessionClaimed: () => void
  setWorkflow: (
    fields: Partial<
      Pick<
        TrialState,
        | "cvId"
        | "cvProcessingJobId"
        | "jobPostId"
        | "jobPostProcessingJobId"
        | "cvProfileVersionId"
        | "matchId"
        | "draftId"
      >
    >
  ) => void
  clearTrialSession: () => void
}

export const useTrialStore = create<TrialState>()(
  persist(
    (set) => ({
      trialSessionId: null,
      expiresAt: null,
      cvId: null,
      cvProcessingJobId: null,
      jobPostId: null,
      jobPostProcessingJobId: null,
      cvProfileVersionId: null,
      matchId: null,
      draftId: null,
      setTrialSession: (trialSessionId, expiresAt) =>
        set({ trialSessionId, expiresAt }),
      // Claiming is one-time and consumes the trial session — clear just
      // that, keeping cvId/jobPostId/matchId/draftId so the continuation
      // screen (and /try/results, now under the real account) can still
      // read them.
      markTrialSessionClaimed: () => set({ trialSessionId: null, expiresAt: null }),
      setWorkflow: (fields) => set(fields),
      clearTrialSession: () =>
        set({
          trialSessionId: null,
          expiresAt: null,
          cvId: null,
          cvProcessingJobId: null,
          jobPostId: null,
          jobPostProcessingJobId: null,
          cvProfileVersionId: null,
          matchId: null,
          draftId: null,
        }),
    }),
    {
      name: "trial-storage",
    }
  )
)
