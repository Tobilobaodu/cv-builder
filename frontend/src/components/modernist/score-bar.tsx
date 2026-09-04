/**
 * The segmented-gradient score bar used identically on Overview, Report
 * detail, and the CVs/Jobs/Recent-matches tables (mockup screens 1-4). Two
 * sizes: "md" is the stacked label/bar/NN-of-100 block (resume-summary
 * grids), "sm" is the compact inline bar+number used inside table cells.
 * `score={null}` renders the mockup's "Scoring…" / em-dash placeholder for
 * a CV or match that hasn't been analysed yet — callers should pass null
 * rather than 0 while a backend analysis job is still running.
 */

import { useSimulatedProgress } from "@/hooks/use-simulated-progress"

export const STRIPED_ACCENT =
  "repeating-linear-gradient(90deg, var(--color-accent) 0 3px, transparent 3px 5px)"
export const STRIPED_NEUTRAL =
  "repeating-linear-gradient(90deg, var(--color-neutral-400) 0 3px, transparent 3px 5px)"

export function ScoreBar({
  score,
  note,
  size = "md",
  width,
  isLoading = false,
  expectedDurationMs,
}: {
  score: number | null
  note?: string
  size?: "md" | "sm"
  width?: number
  /** Pass true while a backend job is actively computing this score (not
   *  merely "no data yet") to fill the bar with a simulated in-progress
   *  animation instead of the flat placeholder — see
   *  hooks/use-simulated-progress.ts for why it's simulated rather than
   *  real: nothing in the backend reports true percent-complete today. */
  isLoading?: boolean
  expectedDurationMs?: number
}) {
  const simulated = useSimulatedProgress(isLoading, expectedDurationMs)
  const clamped = score == null ? null : Math.max(0, Math.min(100, score))
  const barHeight = size === "sm" ? 10 : 16
  const fillPercent = clamped ?? (isLoading ? simulated : null)

  const bar = (
    <div
      style={{
        display: "flex",
        height: barHeight,
        gap: 2,
        width: size === "sm" ? (width ?? 96) : undefined,
      }}
    >
      {fillPercent != null && (
        <div
          style={{ width: `${fillPercent}%`, background: STRIPED_ACCENT, transition: "width 200ms linear" }}
        />
      )}
      <div style={{ flex: 1, background: STRIPED_NEUTRAL }} />
    </div>
  )

  if (size === "sm") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {bar}
        {clamped == null ? (
          <span style={{ fontSize: 13, color: "var(--color-neutral-600)" }}>
            {isLoading ? `${Math.round(simulated)}%` : "—"}
          </span>
        ) : (
          <span style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 13 }}>
            {Math.round(clamped)}
          </span>
        )}
      </div>
    )
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {bar}
      {clamped == null ? (
        <span style={{ fontSize: 13, color: "var(--color-neutral-600)" }}>
          {isLoading ? `Scoring… ${Math.round(simulated)}%` : "Scoring…"}
        </span>
      ) : (
        <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
          <div style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 20 }}>
            {Math.round(clamped)}
            <span style={{ fontSize: 13, color: "var(--color-neutral-600)" }}>/100</span>
          </div>
          {note && (
            <div style={{ fontSize: 12, lineHeight: 1.4, color: "var(--color-neutral-700)" }}>
              {note}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
