/** The Overview screen's 4-column divided stat row (mockup: "stat band"). */
export function StatBand({ stats }: { stats: { label: string; value: React.ReactNode; note?: React.ReactNode; noteColor?: string }[] }) {
  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${stats.length}, 1fr)`,
        borderTop: "1px solid var(--color-divider)",
        borderBottom: "1px solid var(--color-divider)",
      }}
    >
      {stats.map((stat, i) => (
        <div
          key={stat.label}
          style={{
            padding:
              i === 0
                ? "24px 32px 24px 0"
                : i === stats.length - 1
                  ? "24px 0 24px 32px"
                  : "24px 32px",
            borderRight: i === stats.length - 1 ? undefined : "1px solid var(--color-divider)",
          }}
        >
          <div
            style={{
              fontSize: 11,
              letterSpacing: "0.1em",
              textTransform: "uppercase",
              color: "var(--color-neutral-600)",
              marginBottom: 10,
            }}
          >
            {stat.label}
          </div>
          <div style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 32, lineHeight: 1 }}>
            {stat.value}
          </div>
          {stat.note && (
            <div
              style={{
                fontSize: 12,
                marginTop: 6,
                color: stat.noteColor ?? "var(--color-neutral-700)",
              }}
            >
              {stat.note}
            </div>
          )}
        </div>
      ))}
    </section>
  )
}
