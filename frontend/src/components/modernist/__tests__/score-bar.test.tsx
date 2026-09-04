import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, render, screen } from "@testing-library/react"
import { ScoreBar } from "@/components/modernist/score-bar"

describe("ScoreBar", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("renders a static placeholder when there's no score and nothing is loading", () => {
    render(<ScoreBar score={null} />)
    expect(screen.getByText("Scoring…")).toBeInTheDocument()
  })

  it("renders the real score once one exists, ignoring isLoading", () => {
    render(<ScoreBar score={82} isLoading />)
    expect(screen.getByText("82")).toBeInTheDocument()
  })

  it("shows a climbing percentage while isLoading and no score yet", () => {
    render(<ScoreBar score={null} isLoading expectedDurationMs={8000} />)
    expect(screen.getByText(/Scoring…/)).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(3000)
    })
    // Should now show some non-zero percentage rather than the bare "Scoring…"
    expect(screen.getByText(/Scoring… \d+%/)).toBeInTheDocument()
  })

  it("small size shows a percentage instead of an em dash while loading", () => {
    render(<ScoreBar score={null} size="sm" isLoading expectedDurationMs={8000} />)
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(screen.queryByText("—")).not.toBeInTheDocument()
    expect(screen.getByText(/%$/)).toBeInTheDocument()
  })
})
