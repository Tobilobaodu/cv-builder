import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { LinkedInImportHint } from "@/components/linkedin-import-hint"

describe("LinkedInImportHint", () => {
  it("starts collapsed with just the link visible", () => {
    render(<LinkedInImportHint />)
    expect(screen.getByTestId("button-linkedin-import")).toHaveTextContent(
      "No CV file handy? Import from LinkedIn instead"
    )
    expect(screen.queryByTestId("panel-linkedin-import")).not.toBeInTheDocument()
  })

  it("expands to show the steps and a LinkedIn link on click", async () => {
    const user = userEvent.setup()
    render(<LinkedInImportHint />)

    await user.click(screen.getByTestId("button-linkedin-import"))

    expect(screen.getByTestId("panel-linkedin-import")).toBeInTheDocument()
    expect(screen.getByText(/Save to PDF/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Open LinkedIn ↗" })).toHaveAttribute(
      "href",
      "https://www.linkedin.com"
    )
  })

  it("calls onOpen exactly once, only on the transition to open", async () => {
    const onOpen = vi.fn()
    const user = userEvent.setup()
    render(<LinkedInImportHint onOpen={onOpen} />)

    const button = screen.getByTestId("button-linkedin-import")
    await user.click(button) // open
    await user.click(button) // close
    await user.click(button) // open again

    expect(onOpen).toHaveBeenCalledTimes(2)
  })

  it("toggles closed on a second click", async () => {
    const user = userEvent.setup()
    render(<LinkedInImportHint />)

    const button = screen.getByTestId("button-linkedin-import")
    await user.click(button)
    await user.click(button)

    expect(screen.queryByTestId("panel-linkedin-import")).not.toBeInTheDocument()
  })
})
