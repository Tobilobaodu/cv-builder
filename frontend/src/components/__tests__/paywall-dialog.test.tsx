import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PaywallDialog } from "@/components/paywall-dialog"

const push = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}))

describe("PaywallDialog", () => {
  it("renders nothing when closed", () => {
    render(<PaywallDialog open={false} onOpenChange={() => {}} />)
    expect(screen.queryByText("Create your account to continue")).not.toBeInTheDocument()
  })

  it("shows the headline and both CTAs when open", () => {
    render(<PaywallDialog open={true} onOpenChange={() => {}} />)
    expect(screen.getByText("Create your account to continue")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Create account" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Log in" })).toBeInTheDocument()
  })

  it("navigates to /register when Create account is clicked", async () => {
    const user = userEvent.setup()
    render(<PaywallDialog open={true} onOpenChange={() => {}} />)

    await user.click(screen.getByRole("button", { name: "Create account" }))
    expect(push).toHaveBeenCalledWith("/register")
  })

  it("navigates to /login when Log in is clicked", async () => {
    const user = userEvent.setup()
    render(<PaywallDialog open={true} onOpenChange={() => {}} />)

    await user.click(screen.getByRole("button", { name: "Log in" }))
    expect(push).toHaveBeenCalledWith("/login")
  })
})
