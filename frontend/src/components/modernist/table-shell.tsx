/**
 * Modernist equivalent of components/data-table-shell.tsx: shared loading/
 * error/empty chrome around a `.table`, restyled with Modernist tokens
 * instead of shadcn's Skeleton/muted-foreground classes.
 */
export function TableShell({
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
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            style={{
              height: 40,
              background: "var(--color-surface)",
              opacity: 0.6 - i * 0.12,
            }}
          />
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <p style={{ padding: "32px 0", textAlign: "center", fontSize: 14, color: "var(--color-accent-700)" }}>
        {errorMessage}
      </p>
    )
  }

  if (isEmpty) {
    return (
      <p style={{ padding: "32px 0", textAlign: "center", fontSize: 14, color: "var(--color-neutral-700)" }}>
        {emptyMessage}
      </p>
    )
  }

  return <table className="table">{children}</table>
}
