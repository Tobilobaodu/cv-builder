import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render } from "@testing-library/react"
import { ProgressBar } from "@/components/modernist/progress-bar"

describe("ProgressBar", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("renders nothing when inactive", () => {
    const { container } = render(<ProgressBar isActive={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("renders a bar when active", () => {
    const { container } = render(<ProgressBar isActive expectedDurationMs={8000} />)
    expect(container.firstChild).not.toBeNull()
  })
})
