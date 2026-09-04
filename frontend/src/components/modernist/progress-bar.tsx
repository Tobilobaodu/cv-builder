import { STRIPED_ACCENT, STRIPED_NEUTRAL } from "@/components/modernist/score-bar"
import { useSimulatedProgress } from "@/hooks/use-simulated-progress"

/**
 * The same striped-red fill ScoreBar uses, for non-score "an action is
 * running" moments (generation, a check, an export) — pass `isActive` and
 * this handles the simulated climb itself (see
 * hooks/use-simulated-progress.ts). Renders nothing once `isActive` is
 * false, so callers keep controlling when it appears/disappears via their
 * own existing status checks.
 */
export function ProgressBar({
  isActive,
  expectedDurationMs,
  height = 8,
  width,
}: {
  isActive: boolean
  expectedDurationMs?: number
  height?: number
  width?: number | string
}) {
  const progress = useSimulatedProgress(isActive, expectedDurationMs)

  if (!isActive) return null

  return (
    <div style={{ display: "flex", height, width, gap: 2 }}>
      <div
        style={{ width: `${progress}%`, background: STRIPED_ACCENT, transition: `width ${150}ms linear` }}
      />
      <div style={{ flex: 1, background: STRIPED_NEUTRAL }} />
    </div>
  )
}
