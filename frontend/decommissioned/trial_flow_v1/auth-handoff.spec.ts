import path from "path"
import { test, expect } from "@playwright/test"

// Sprint 3 e2e: anonymous trial -> hit the cover-letter paywall -> register
// -> verify continuity (the same match/CV reappear post-login, not a fresh
// flow), against the real backend. Reuses Sprint 2's job/CV pairing (see
// trial-flow.spec.ts for why this specific pairing matters) since reaching
// the paywall button requires a successfully generated tailored CV, which
// depends on the evidence binder finding real, matching content.
const REAL_CV_PATH = path.resolve(
  __dirname,
  "..",
  "..",
  "Test Cvs",
  "Tobiloba_Odu_CV.pdf"
)

// Uses phrasing lifted directly from the CV's own listed skills (Figma, UX
// research, usability testing, wireframing, design systems, accessibility/
// WCAG 2.1, CRO, stakeholder management, workshop facilitation) to give the
// evidence binder the clearest possible support and minimize this test's
// exposure to real generation non-determinism (see trial-flow.spec.ts).
const JOB_TEXT =
  "Senior Product Designer\n\n" +
  "We are looking for an experienced product designer to lead UX across our platform.\n\n" +
  "Requirements:\n- Figma\n- UX research\n- usability testing\n- wireframing\n- design systems\n- accessibility, WCAG 2.1\n- CRO\n- information architecture\n\n" +
  "Preferred:\n- stakeholder management\n- workshop facilitation\n- user journeys\n- component libraries"

test("anonymous trial -> paywall -> register -> continuity into the authenticated dashboard", async ({
  page,
}) => {
  test.setTimeout(5 * 60_000)

  await page.goto("/try")
  await page.waitForURL(/\/try\/upload$/, { timeout: 15_000 })

  await page.locator("#cv-file").setInputFiles(REAL_CV_PATH)
  await page.getByLabel("Job description").fill(JOB_TEXT)
  await page.getByRole("button", { name: "Run my match" }).click()

  await page.waitForURL(/\/try\/results$/, { timeout: 15_000 })

  const success = page.getByText("Your tailored CV", { exact: true })
  const failure = page.getByText(/couldn't find enough matching, verifiable experience/)
  await expect(success.or(failure)).toBeVisible({ timeout: 3 * 60_000 })

  test.skip(
    await failure.isVisible(),
    "Generation didn't produce evidence-backed content this run (non-deterministic — see trial-flow.spec.ts) — the paywall CTA only appears alongside a successful draft, so this run can't exercise the handoff."
  )

  await page.getByRole("button", { name: "Create a cover letter for this job" }).click()
  await expect(page.getByText("Create your account to continue")).toBeVisible()

  await page.getByRole("button", { name: "Create account" }).click()
  await page.waitForURL(/\/register$/, { timeout: 10_000 })

  const email = `e2e-handoff-${Date.now()}@test.com`
  const password = "Handoff1234!"
  await page.getByLabel("Email").fill(email)
  await page.getByLabel("Password", { exact: true }).fill(password)
  await page.getByLabel("Confirm password").fill(password)
  await page.getByRole("button", { name: "Create account" }).click()

  // Claim happens post-auth — lands on the continuation screen, not the bare dashboard.
  await page.waitForURL(/\/dashboard\/continue$/, { timeout: 20_000 })
  await expect(page.getByText("Welcome! Your trial is now saved")).toBeVisible()

  await page.getByRole("button", { name: "View your results" }).click()

  // Same match/CV, now under the real account (Bearer token, not the
  // trial header) — not a fresh /try flow.
  await page.waitForURL(/\/try\/results$/, { timeout: 10_000 })
  await expect(page.getByText(/Match score: \d+/)).toBeVisible()
  await expect(page.getByText("Your tailored CV", { exact: true })).toBeVisible()
  // Now authenticated, so the cover-letter CTA is a disabled "coming soon"
  // state, not the paywall it was before.
  await expect(
    page.getByRole("button", { name: "Create a cover letter for this job" })
  ).toBeDisabled()
})
