import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { Sidebar } from "@/components/modernist/sidebar"

let currentPathname = "/dashboard"
vi.mock("next/navigation", () => ({
  usePathname: () => currentPathname,
}))

describe("Sidebar", () => {
  it("renders links for every dashboard section", () => {
    currentPathname = "/dashboard"
    render(<Sidebar />)

    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "CVs" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Jobs" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Reports" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Cover letters" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "New match" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Settings & billing" })).toBeInTheDocument()
  })

  it("marks the Overview link active only on the exact /dashboard path, not sub-routes", () => {
    currentPathname = "/dashboard/cvs"
    render(<Sidebar />)

    expect(screen.getByRole("link", { name: "Overview" })).toHaveStyle({ background: "transparent" })
    expect(screen.getByRole("link", { name: "CVs" })).toHaveStyle({ background: "var(--color-text)" })
  })
})
