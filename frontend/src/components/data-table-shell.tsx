import { Table } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"

/**
 * Shared loading/error/empty-state chrome around a <Table> — the actual
 * columns/rows are supplied by each page (CVs, jobs, etc. have genuinely
 * different shapes), but the three non-happy-path states are identical
 * everywhere, so they live here once instead of four times.
 */
export function DataTableShell({
  isLoading,
  isError,
  isEmpty,
  emptyMessage,
  errorMessage = "Couldn't load this list. Please try again.",
  children,
}: {
  isLoading: boolean
  isError: boolean
  isEmpty: boolean
  emptyMessage: string
  errorMessage?: string
  children: React.ReactNode
}) {
  if (isLoading) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }

  if (isError) {
    return <p className="py-8 text-center text-sm text-destructive">{errorMessage}</p>
  }

  if (isEmpty) {
    return <p className="py-8 text-center text-sm text-muted-foreground">{emptyMessage}</p>
  }

  return <Table>{children}</Table>
}
