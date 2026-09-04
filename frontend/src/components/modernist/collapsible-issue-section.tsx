"use client"

import { useState } from "react"
import { Check, ChevronDown, X } from "lucide-react"

type IssueRow = { passed: boolean; title: string; detail: string }

/**
 * The chevron-toggle section used for Report detail's ATS Readiness and
 * Formatting blocks (pass/fail icon rows via `issues`) and its Tips block
 * (numbered rows via `tips`). Pass exactly one of `issues`/`tips`.
 */
export function CollapsibleIssueSection({
  title,
  countLabel,
  countColor,
  description,
  issues,
  tips,
  defaultOpen = true,
}: {
  title: string
  countLabel?: React.ReactNode
  countColor?: string
  description?: string
  issues?: IssueRow[]
  tips?: string[]
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const rowCount = (issues?.length ?? 0) + (tips?.length ?? 0)

  return (
    <section>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 24 }}>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            border: 0,
            background: "transparent",
            padding: 0,
            cursor: "pointer",
            color: "inherit",
            font: "inherit",
          }}
        >
          <h3 style={{ fontSize: 25, margin: 0 }}>{title}</h3>
          <ChevronDown
            width={18}
            height={18}
            strokeWidth={2.4}
            style={{
              color: "var(--color-accent)",
              transition: "transform .15s",
              transform: open ? undefined : "rotate(-90deg)",
            }}
          />
        </button>
        {countLabel != null && (
          <div style={{ fontSize: 13, fontWeight: 600, color: countColor ?? "var(--color-accent-700)" }}>
            {countLabel}
          </div>
        )}
      </div>
      {open && (
        <div style={{ marginTop: 16 }}>
          {description && (
            <p
              style={{
                margin: "0 0 20px",
                fontSize: 14,
                lineHeight: 1.6,
                color: "var(--color-neutral-800)",
                maxWidth: "72ch",
              }}
            >
              {description}
            </p>
          )}
          <div style={{ display: "flex", flexDirection: "column" }}>
            {issues?.map((issue, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  gap: 14,
                  padding: "14px 0",
                  borderTop: "1px solid var(--color-divider)",
                  borderBottom: i === rowCount - 1 ? "1px solid var(--color-divider)" : undefined,
                }}
              >
                {issue.passed ? (
                  <Check
                    width={18}
                    height={18}
                    strokeWidth={2.4}
                    strokeLinecap="square"
                    style={{ color: "var(--color-text)", flex: "none", marginTop: 2 }}
                  />
                ) : (
                  <X
                    width={18}
                    height={18}
                    strokeWidth={2.4}
                    strokeLinecap="square"
                    style={{ color: "var(--color-accent)", flex: "none", marginTop: 2 }}
                  />
                )}
                <div style={{ fontSize: 14, lineHeight: 1.5 }}>
                  {issue.title && <strong>{issue.title}. </strong>}
                  {issue.detail}
                </div>
              </div>
            ))}
            {tips?.map((tip, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  gap: 14,
                  padding: "14px 0",
                  borderTop: "1px solid var(--color-divider)",
                  borderBottom: i === rowCount - 1 ? "1px solid var(--color-divider)" : undefined,
                }}
              >
                <div
                  style={{
                    fontFamily: "var(--font-heading)",
                    fontWeight: 800,
                    fontSize: 13,
                    color: "var(--color-accent)",
                    flex: "none",
                    width: 18,
                  }}
                >
                  {String(i + 1).padStart(2, "0")}
                </div>
                <div style={{ fontSize: 14, lineHeight: 1.5 }}>{tip}</div>
              </div>
            ))}
            {rowCount === 0 && (
              <p style={{ fontSize: 13, color: "var(--color-neutral-700)", padding: "14px 0" }}>
                Nothing to show yet.
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
