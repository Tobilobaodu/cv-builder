"use client"

import { useEffect, useRef, useState } from "react"

const CEILING = 94
const TICK_MS = 150

/**
 * Simulates a 0-100 progress percentage for an async action with no real
 * backend progress signal. processing_jobs.progress exists as a DB column
 * but nothing writes to it and it isn't exposed by any API response — so
 * this is a deliberate "feels alive" fill, not a measurement, calibrated
 * per call site via `expectedDurationMs`.
 *
 * Climbs along an exponential-approach curve (fast at first, slowing down)
 * and is capped at 94% while active — it must never claim completion on
 * its own, since it has no way to know the action actually finished. The
 * caller swaps it out for the real result once the underlying job reports
 * done.
 *
 * `progress` state is only ever written from inside a timer callback, never
 * synchronously in the effect body (a `setTimeout(tick, 0)` kickoff stands
 * in for an immediate call) — calling setState directly in an effect body
 * is a lint error here (react-hooks/set-state-in-effect) and would also
 * mean a re-run reusing this same mounted component briefly flashes the
 * *previous* run's leftover value before the first real tick corrects it.
 */
export function useSimulatedProgress(isActive: boolean, expectedDurationMs = 8000): number {
  const [progress, setProgress] = useState(0)
  const startRef = useRef<number | null>(null)

  useEffect(() => {
    if (!isActive) return

    startRef.current = Date.now()
    // tau chosen so the bar sits around ~85% right at expectedDurationMs —
    // close enough to "done" to read as almost-there for the common case,
    // with room left to keep creeping if the real action runs long.
    const tau = expectedDurationMs / 2.5

    function tick() {
      const elapsed = Date.now() - (startRef.current ?? Date.now())
      setProgress(CEILING * (1 - Math.exp(-elapsed / tau)))
    }

    const kickoff = setTimeout(tick, 0)
    const interval = setInterval(tick, TICK_MS)
    return () => {
      clearTimeout(kickoff)
      clearInterval(interval)
    }
  }, [isActive, expectedDurationMs])

  return isActive ? progress : 0
}
