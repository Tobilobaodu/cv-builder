import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Topbar } from "@/components/modernist/topbar"
import { useAuthStore } from "@/store/auth-store"

describe("Topbar", () => {
  it("shows the current user's email and initials", () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "jane.doe@example.com" })
    render(<Topbar crumb="Workspace / Overview" />)

    expect(screen.getByText("jane.doe@example.com")).toBeInTheDocument()
    expect(screen.getByText("JD")).toBeInTheDocument()
  })

  it("logs out and clears auth state when the logout button is clicked", async () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "jane.doe@example.com" })
    const user = userEvent.setup()
    render(<Topbar crumb="Workspace / Overview" />)

    await user.click(screen.getByRole("button", { name: "Log out" }))

    expect(useAuthStore.getState().accessToken).toBeNull()
  })
})
