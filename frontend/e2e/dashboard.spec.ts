import path from "path"
import { test, expect } from "@playwright/test"

// Sprint 4 e2e: an uploaded CV appears in the authenticated dashboard's CVs
// list, against the real backend. Registers directly (not via the trial
// flow) and uploads while already authenticated — /cvs is trial-OR-account
// accessible (get_current_user_or_trial_session), so an authenticated user
// can use /try/upload directly with no trial session at all. This avoids
// depending on match creation or tailored-CV generation (see
// auth-handoff.spec.ts for why those are slow and non-deterministic) —
// this test only needs the upload itself to land.
const REAL_CV_PATH = path.resolve(
  __dirname,
  "..",
  "..",
  "Test Cvs",
  "Tobiloba_Odu_CV.pdf"
)

test("an uploaded CV appears in the dashboard CVs list", async ({ page }) => {
  test.setTimeout(2 * 60_000)

  const email = `e2e-dashboard-${Date.now()}@test.com`
  const password = "Dashboard123!"

  await page.goto("/register")
  await page.getByLabel("Email").fill(email)
  await page.getByLabel("Password", { exact: true }).fill(password)
  await page.getByLabel("Confirm password").fill(password)
  await page.getByRole("button", { name: "Create account" }).click()
  await page.waitForURL(/\/dashboard$/, { timeout: 15_000 })

  // /try/upload now uploads on file selection, so nothing else is needed
  // here — waiting for extraction to land is what proves the upload stuck.
  await page.goto("/try/upload")
  await page.getByTestId("input-cv-file").setInputFiles(REAL_CV_PATH)
  await expect(page.getByTestId("status-ready")).toBeVisible({ timeout: 120_000 })

  await page.goto("/dashboard/cvs")
  await expect(page.getByText("Tobiloba_Odu_CV.pdf")).toBeVisible({ timeout: 15_000 })

  await page.goto("/dashboard")
  await expect(page.getByText("Tobiloba_Odu_CV.pdf")).toBeVisible({ timeout: 15_000 })
})
