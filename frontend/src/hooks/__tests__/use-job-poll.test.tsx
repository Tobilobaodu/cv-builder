import { describe, expect, it } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import { createQueryWrapper } from "@/test/query-wrapper"
import { useJobPoll } from "@/hooks/use-job-poll"

const BASE = "http://localhost:8000/api/v1"

function jobResponse(status: string) {
  return {
    id: "job-1",
    jobType: "cv_parse",
    sourceEntityType: "cv_file",
    sourceEntityId: "cv-1",
    status,
    retryCount: 0,
    lastError: null,
    createdAt: new Date().toISOString(),
    completedAt: status === "completed" ? new Date().toISOString() : null,
  }
}

describe("useJobPoll", () => {
  it("does nothing when jobId is null", () => {
    const { result } = renderHook(() => useJobPoll(null), {
      wrapper: createQueryWrapper(),
    })
    expect(result.current.job).toBeUndefined()
    expect(result.current.isPolling).toBe(false)
  })

  it("polls until the job reaches a completed status", async () => {
    let callCount = 0
    server.use(
      http.get(`${BASE}/jobs/job-1`, () => {
        callCount += 1
        return HttpResponse.json(jobResponse(callCount < 2 ? "processing" : "completed"))
      })
    )

    const { result } = renderHook(() => useJobPoll("job-1"), {
      wrapper: createQueryWrapper(),
    })

    await waitFor(() => expect(result.current.isCompleted).toBe(true), { timeout: 5000 })
    expect(result.current.isPolling).toBe(false)
    expect(callCount).toBeGreaterThanOrEqual(2)
  })

  it("polls fast enough that a job needing 4 polls completes well under the old 2/4/8/15s backoff's 14s", async () => {
    // Not asserting an exact interval (flaky against real timers/MSW) —
    // asserting the old backoff's math would have blown this budget while
    // the new POLL_FAST_MS=1000 cadence (jbs-solution-sheet.md S5) clears
    // it comfortably: old cost for 4 polls is 2+4+8=14s before the 4th
    // request even fires; new cost is ~3s.
    let callCount = 0
    server.use(
      http.get(`${BASE}/jobs/job-fast`, () => {
        callCount += 1
        return HttpResponse.json(jobResponse(callCount < 4 ? "processing" : "completed"))
      })
    )

    const { result } = renderHook(() => useJobPoll("job-fast"), {
      wrapper: createQueryWrapper(),
    })

    await waitFor(() => expect(result.current.isCompleted).toBe(true), { timeout: 8000 })
    expect(callCount).toBe(4)
  })

  it("reports isFailed and stops polling when the job fails", async () => {
    server.use(
      http.get(`${BASE}/jobs/job-2`, () => HttpResponse.json(jobResponse("failed")))
    )

    const { result } = renderHook(() => useJobPoll("job-2"), {
      wrapper: createQueryWrapper(),
    })

    await waitFor(() => expect(result.current.isFailed).toBe(true))
    expect(result.current.isPolling).toBe(false)
    expect(result.current.isCompleted).toBe(false)
  })
})
