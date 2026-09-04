import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, renderHook } from "@testing-library/react"
import { useSimulatedProgress } from "@/hooks/use-simulated-progress"

describe("useSimulatedProgress", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("returns 0 when never active", () => {
    const { result } = renderHook(() => useSimulatedProgress(false, 8000))
    expect(result.current).toBe(0)
  })

  it("climbs above 0 shortly after becoming active", () => {
    const { result } = renderHook(() => useSimulatedProgress(true, 8000))
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(result.current).toBeGreaterThan(0)
  })

  it("never reaches 100 no matter how long it runs", () => {
    const { result } = renderHook(() => useSimulatedProgress(true, 8000))
    act(() => {
      vi.advanceTimersByTime(10 * 60 * 1000) // 10 minutes — way past any real completion
    })
    expect(result.current).toBeLessThan(100)
    expect(result.current).toBeGreaterThan(80) // should have climbed close to its ceiling by then
  })

  it("resets to 0 immediately once isActive goes false", () => {
    const { result, rerender } = renderHook(
      ({ isActive }) => useSimulatedProgress(isActive, 8000),
      { initialProps: { isActive: true } }
    )
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(result.current).toBeGreaterThan(0)

    rerender({ isActive: false })
    expect(result.current).toBe(0)
  })

  it("starts a fresh climb rather than resuming the previous run's value", () => {
    const { result, rerender } = renderHook(
      ({ isActive }) => useSimulatedProgress(isActive, 8000),
      { initialProps: { isActive: true } }
    )
    act(() => {
      vi.advanceTimersByTime(8000)
    })
    const firstRunProgress = result.current
    expect(firstRunProgress).toBeGreaterThan(50)

    rerender({ isActive: false })
    rerender({ isActive: true })
    act(() => {
      vi.advanceTimersByTime(150)
    })

    expect(result.current).toBeLessThan(firstRunProgress)
  })
})
