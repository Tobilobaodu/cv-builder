import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Navbar } from "@/components/navbar"
import { useAuthStore } from "@/store/auth-store"

const push = vi.fn()
let currentPathname = "/"
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => currentPathname,
}))

describe("Navbar", () => {
  it("shows Log in and Try for free when logged out", () => {
    render(<Navbar />)
    expect(screen.getByRole("link", { name: "Log in" })).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "Try for free" })
    ).toBeInTheDocument()
  })

  it("shows the user's email and no Log in link when logged in", () => {
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })
    render(<Navbar />)

    expect(screen.getByRole("button", { name: "a@b.com" })).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Log in" })
    ).not.toBeInTheDocument()
  })

  it("logs out and redirects to / when on a public page", async () => {
    currentPathname = "/"
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })
    const user = userEvent.setup()
    render(<Navbar />)

    await user.click(screen.getByRole("button", { name: "a@b.com" }))
    await user.click(await screen.findByText("Log out"))

    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(push).toHaveBeenCalledWith("/")
  })

  it("logs out without navigating when on a /dashboard page (the route guard handles it)", async () => {
    currentPathname = "/dashboard/cvs"
    useAuthStore.getState().setAuth("token-1", { id: "u1", email: "a@b.com" })
    const user = userEvent.setup()
    render(<Navbar />)

    await user.click(screen.getByRole("button", { name: "a@b.com" }))
    await user.click(await screen.findByText("Log out"))

    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(push).not.toHaveBeenCalled()
  })
})
