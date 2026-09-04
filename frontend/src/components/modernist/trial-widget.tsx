import Link from "next/link"

/**
 * Sidebar's free-trial progress widget. Static content, matching the
 * mockup's values exactly ("1 of 3 rewrites used") — there is no billing
 * backend yet, so this is a deliberate product decision (see task spec),
 * not a placeholder that needs wiring later.
 */
export function TrialWidget() {
  return (
    <div
      style={{
        borderTop: "1px solid var(--color-divider)",
        padding: "20px 24px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-600)" }}>
        Free trial
      </div>
      <div style={{ display: "flex", height: 8, gap: 2 }}>
        <div style={{ width: "33%", background: "var(--color-accent)" }} />
        <div style={{ flex: 1, background: "var(--color-neutral-300)" }} />
      </div>
      <div style={{ fontSize: 12, color: "var(--color-neutral-700)" }}>1 of 3 rewrites used</div>
      <Link href="/dashboard/settings" className="btn btn-secondary btn-block">
        Upgrade plan
      </Link>
    </div>
  )
}
