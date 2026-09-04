import { test, expect } from "@playwright/test"

// Hydration guard. /try/upload is a client component that reads two zustand
// stores backed by localStorage, so it is exactly the shape of page that can
// mismatch between the server render (no storage) and the client render
// (storage rehydrated synchronously on import).
//
// Note: no waitUntil "networkidle" here — Next's dev server holds an HMR
// websocket open, so networkidle never fires and the test would just hang.

function collectProblems(page: import("@playwright/test").Page): string[] {
  const problems: string[] = []
  page.on("console", (msg) => {
    if (msg.type() === "error") problems.push(msg.text())
  })
  page.on("pageerror", (err) => problems.push(String(err)))
  return problems
}

test("/try/upload hydrates without a mismatch", async ({ page }) => {
  const problems = collectProblems(page)

  await page.goto("/try/upload")
  await expect(page.getByTestId("button-analyse")).toBeDisabled()
  await expect(page.getByTestId("state-empty")).toBeVisible()

  expect(problems.join("\n")).not.toMatch(/hydrat|did not match|didn't match/i)
})

test("/try/upload hydrates cleanly with persisted store state", async ({
  page,
}) => {
  const problems = collectProblems(page)

  // Seed both persisted stores before the page renders. zustand's persist
  // middleware rehydrates synchronously from localStorage on import, so the
  // client's first render sees these values while the server's never can.
  // That asymmetry is the classic source of the mismatch this file guards.
  await page.addInitScript(() => {
    localStorage.setItem(
      "trial-storage",
      JSON.stringify({
        state: {
          trialSessionId: "seeded-session",
          expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
        },
        version: 0,
      })
    )
  })

  await page.goto("/try/upload")
  await expect(page.getByTestId("button-analyse")).toBeDisabled()
  await expect(page.getByTestId("state-empty")).toBeVisible()

  expect(problems.join(" | ")).not.toMatch(/hydrat|did not match|didn't match/i)
})
