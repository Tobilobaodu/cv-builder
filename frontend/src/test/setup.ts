import "@testing-library/jest-dom/vitest"
import { afterAll, afterEach, beforeAll, vi } from "vitest"
import { cleanup } from "@testing-library/react"
import { server } from "./msw/server"
import { useAuthStore } from "@/store/auth-store"
import { useTrialStore } from "@/store/trial-store"

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
  server.resetHandlers()
  cleanup()
  window.localStorage.clear()
  useAuthStore.setState({ accessToken: null, refreshToken: null, user: null })
  useTrialStore.setState({
    trialSessionId: null,
    expiresAt: null,
    cvId: null,
    cvProcessingJobId: null,
    jobPostId: null,
    jobPostProcessingJobId: null,
    cvProfileVersionId: null,
    matchId: null,
    draftId: null,
  })
  vi.clearAllMocks()
})
afterAll(() => server.close())
