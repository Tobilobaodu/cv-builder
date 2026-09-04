import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ContinueWhereYouLeftOffPage from "@/app/dashboard/continue/page"
import { useTrialStore } from "@/store/trial-store"

const push = vi.fn()
const replace = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
}))

describe("ContinueWhereYouLeftOffPage", () => {
  it("shows the welcome screen when trial workflow data is present", () => {
    useTrialStore.setState({ cvId: "cv-1", matchId: "match-1" })
    render(<ContinueWhereYouLeftOffPage />)

    expect(screen.getByText("Welcome! Your trial is now saved")).toBeInTheDocument()
    expect(replace).not.toHaveBeenCalled()
  })

  it("redirects to /dashboard when there is no trial workflow data", async () => {
    useTrialStore.setState({ cvId: null, matchId: null })
    render(<ContinueWhereYouLeftOffPage />)

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"))
  })

  it("navigates to /try/results when View your results is clicked", async () => {
    useTrialStore.setState({ cvId: "cv-1", matchId: "match-1" })
    const user = userEvent.setup()
    render(<ContinueWhereYouLeftOffPage />)

    await user.click(screen.getByRole("button", { name: "View your results" }))
    expect(push).toHaveBeenCalledWith("/try/results")
  })
})
