import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { DataTableShell } from "@/components/data-table-shell"
import { TableBody, TableRow, TableCell } from "@/components/ui/table"

describe("DataTableShell", () => {
  it("shows loading skeletons while isLoading", () => {
    const { container } = render(
      <DataTableShell isLoading isError={false} isEmpty={false} emptyMessage="empty">
        <TableBody />
      </DataTableShell>
    )
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)
  })

  it("shows the error message when isError", () => {
    render(
      <DataTableShell isLoading={false} isError isEmpty={false} emptyMessage="empty">
        <TableBody />
      </DataTableShell>
    )
    expect(screen.getByText("Couldn't load this list. Please try again.")).toBeInTheDocument()
  })

  it("shows a custom error message when provided", () => {
    render(
      <DataTableShell
        isLoading={false}
        isError
        isEmpty={false}
        emptyMessage="empty"
        errorMessage="Custom failure."
      >
        <TableBody />
      </DataTableShell>
    )
    expect(screen.getByText("Custom failure.")).toBeInTheDocument()
  })

  it("shows the empty message when isEmpty", () => {
    render(
      <DataTableShell isLoading={false} isError={false} isEmpty emptyMessage="Nothing here yet.">
        <TableBody />
      </DataTableShell>
    )
    expect(screen.getByText("Nothing here yet.")).toBeInTheDocument()
  })

  it("renders the table content in the happy path", () => {
    render(
      <DataTableShell isLoading={false} isError={false} isEmpty={false} emptyMessage="empty">
        <TableBody>
          <TableRow>
            <TableCell>Row content</TableCell>
          </TableRow>
        </TableBody>
      </DataTableShell>
    )
    expect(screen.getByText("Row content")).toBeInTheDocument()
  })
})
