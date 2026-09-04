"use client"

import { useReportWebVitals } from "next/web-vitals"

/**
 * Console-only for now (dev-visible, zero backend dependency) — this is
 * the collection point the perf addendum's checklist asks for; wiring it
 * to a real analytics/APM endpoint is a follow-up once one exists, not
 * something to fabricate here (see loadtest/README.md's same reasoning
 * about not inventing infra this repo doesn't have yet).
 */
export function WebVitalsReporter() {
  useReportWebVitals((metric) => {
    if (process.env.NODE_ENV !== "production") {
      console.debug(`[web-vitals] ${metric.name}`, {
        value: metric.value,
        rating: metric.rating,
        id: metric.id,
      })
    }
  })

  return null
}
