/** Report detail's 5-column supported/partial/unsupported/contradictory/unclear band. */
export function EvidenceBand({
  supported,
  partial,
  unsupported,
  contradictory,
  unclear,
}: {
  supported: number
  partial: number
  unsupported: number
  contradictory: number
  unclear: number
}) {
  const cells: { label: string; value: number; accent?: boolean }[] = [
    { label: "Supported", value: supported },
    { label: "Partial", value: partial },
    { label: "Unsupported", value: unsupported, accent: true },
    { label: "Contradictory", value: contradictory },
    { label: "Unclear", value: unclear },
  ]

  return (
    <section
      style={{
        borderTop: "1px solid var(--color-divider)",
        borderBottom: "1px solid var(--color-divider)",
        display: "grid",
        gridTemplateColumns: "repeat(5, 1fr)",
      }}
    >
      {cells.map((cell, i) => (
        <div
          key={cell.label}
          style={{
            padding: i === 0 ? "20px 16px 20px 0" : i === cells.length - 1 ? "20px 0 20px 16px" : "20px 16px",
            borderRight: i === cells.length - 1 ? undefined : "1px solid var(--color-divider)",
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-heading)",
              fontWeight: 800,
              fontSize: 25,
              lineHeight: 1,
              color: cell.accent ? "var(--color-accent-700)" : undefined,
            }}
          >
            {cell.value}
          </div>
          <div
            style={{
              fontSize: 11,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: "var(--color-neutral-600)",
              marginTop: 8,
            }}
          >
            {cell.label}
          </div>
        </div>
      ))}
    </section>
  )
}
