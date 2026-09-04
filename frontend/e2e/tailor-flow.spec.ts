import path from "path"
import { test, expect } from "@playwright/test"

// e2e for /try/upload — the single-call rewrite flow. Runs against the real
// docker compose stack (API on :8000) and makes a real LLM call, so the
// analyse step is given a generous timeout.
//
// Covers the three behaviours this page exists for:
//   1. Upload starts on file selection, with no submit button.
//   2. The extracted CV text is shown in a left slide-over.
//   3. Stats and matching criteria render once the analysis completes.

const REAL_CV_PATH = path.resolve(
  __dirname,
  "..",
  "..",
  "Test Cvs",
  "Tobiloba_Odu_CV.pdf"
)

const JOB_POST_URL = "https://example.com"

const JOB_TEXT = `Senior Product Designer — Digital Banking Platform
London (Hybrid)

Responsibilities
- Own the end-to-end design process for major customer journeys, from discovery research through to shipped product.
- Run generative and evaluative research, including usability testing, and turn findings into design decisions.
- Contribute to and extend our design system, including design tokens and component libraries.
- Produce wireframes, high fidelity UI and interactive prototypes in Figma.

Requirements
- 5+ years of product design experience, ideally in fintech, banking or another regulated industry.
- Expert level Figma skills, including auto layout, variables and prototyping.
- Demonstrable experience with design systems and component libraries.
- Strong UX research skills including usability testing and survey design.
- Working knowledge of accessibility standards, specifically WCAG 2.1 AA.
- Experience with conversion rate optimisation and analytics informed design.
- Excellent stakeholder management and communication skills.`

// The paywall is off while the single-call flow is evaluated. It is off by
// being unreachable, not by being deleted: /try/results is the only page
// that renders PaywallDialog, and it self-redirects unless the trial store
// holds a cvId and jobPostId, which the new flow never writes. This test
// exists so that stays true — if a change starts populating that state, the
// retired paywall would silently come back with a dead pipeline behind it.
test("the paywall is unreachable: /try/results bounces to the live flow", async ({
  page,
}) => {
  await page.goto("/try/results")
  await page.waitForURL(/\/try\/upload$/, { timeout: 20_000 })
  await expect(page.getByTestId("state-empty")).toBeVisible()
  await expect(page.getByText("Create your account to continue")).toBeHidden()
})

// The job post can also come from a URL, and on that tab "Tailor my CV" is
// the only button — it fetches the posting and then analyses it. Both halves
// run against the real SSRF-guarded fetcher in worker_job_fetch: the
// link-local address is the canonical cloud-metadata SSRF target and must be
// refused with a reason the user can act on, and a public URL must end up as
// editable text in the textarea rather than an opaque server-side reference.
test("job post from a URL: one button fetches, refuses link-local, then analyses", async ({
  page,
}) => {
  test.setTimeout(240_000)

  await page.goto("/try/upload")
  await page.getByTestId("input-cv-file").setInputFiles(REAL_CV_PATH)
  await expect(page.getByTestId("status-ready")).toBeVisible({ timeout: 120_000 })

  // The extracted-CV slide-over opens itself and covers the page, so it has
  // to be dismissed before anything else is reachable.
  await page.getByTestId("button-close-extracted").click()
  await expect(page.getByTestId("modal-extracted-cv")).toBeHidden()

  await page.getByTestId("tab-job-url").click()
  await expect(page.getByTestId("button-fetch-job-url")).toHaveCount(0)

  await page
    .getByTestId("input-job-url")
    .fill("http://169.254.169.254/latest/meta-data/")
  await page.getByTestId("button-analyse").click()
  await expect(page.getByTestId("status-job-fetch-failed")).toContainText(
    /rejected for security reasons|Could not fetch/,
    { timeout: 60_000 }
  )
  // A refused fetch must stop there, not fall through into a rewrite.
  await expect(page.getByTestId("state-complete")).toBeHidden()

  await page.getByTestId("input-job-url").fill(JOB_POST_URL)
  await page.getByTestId("button-analyse").click()

  // Success switches back to the paste tab with the text filled in, then
  // runs the analysis without a second click.
  await expect(page.getByTestId("status-job-fetched")).toBeVisible({
    timeout: 60_000,
  })
  const fetched = await page.getByTestId("input-job-description").inputValue()
  expect(fetched.length).toBeGreaterThan(0)
  // worker_job_fetch strips the markup before storing, so what reaches the
  // textarea — and the model — is text, not the page's HTML source.
  expect(fetched).not.toContain("<")
  expect(fetched.toLowerCase()).not.toContain("<script")
  await expect(page.getByTestId("state-complete")).toBeVisible({
    timeout: 180_000,
  })
})

test.describe("tailor flow", () => {
  test("uploads on selection, shows extracted CV, then renders stats", async ({
    page,
  }) => {
    test.setTimeout(240_000)

    await page.goto("/try/upload")
    await expect(page.getByTestId("state-empty")).toBeVisible()

    // 1. Upload begins on selection — no submit button is pressed here.
    await page.getByTestId("input-cv-file").setInputFiles(REAL_CV_PATH)
    await expect(page.getByTestId("status-ready")).toBeVisible({
      timeout: 120_000,
    })

    // 2. The slide-over opens by itself once text is available, and holds
    //    the real extracted text.
    const panel = page.getByTestId("modal-extracted-cv")
    await expect(panel).toBeVisible()
    const extracted = page.getByTestId("text-extracted-cv")
    await expect(extracted).toContainText("TOBILOBA ODU")
    // The section the old structured parser used to swallow into the
    // previous role — present here because this flow reads raw text.
    await expect(extracted).toContainText("Earlier Career")
    await expect(extracted).toContainText("Karrox")

    await page.getByTestId("button-close-extracted").click()
    await expect(panel).toBeHidden()

    // Re-openable from the ready card.
    await page.getByTestId("button-view-extracted").click()
    await expect(page.getByTestId("modal-extracted-cv")).toBeVisible()
    await page.getByTestId("button-close-extracted").click()

    // 3. Analyse, then assert the stats surface.
    await expect(page.getByTestId("button-analyse")).toBeDisabled()
    await page.getByTestId("input-target-title").fill("Senior Product Designer")
    await page.getByTestId("input-job-description").fill(JOB_TEXT)
    await expect(page.getByTestId("button-analyse")).toBeEnabled()
    await page.getByTestId("button-analyse").click()

    await expect(page.getByTestId("state-complete")).toBeVisible({
      timeout: 180_000,
    })
    await expect(page.getByTestId("metric-ats-score")).toBeVisible()
    await expect(page.getByTestId("text-match-label")).not.toBeEmpty()
    await expect(page.getByTestId("list-matched")).toBeVisible()
    await expect(page.getByTestId("list-keywords")).toBeVisible()
    await expect(page.getByTestId("list-match-notes")).toBeVisible()
    await expect(page.getByTestId("text-tailored-cv")).toContainText(
      "Professional Summary"
    )

    // Download the PDF. The rewrite persists nothing, so the Markdown is
    // posted back to be rendered — this proves the API can actually reach
    // gotenberg, which sits on the egress-free network.
    const downloadPromise = page.waitForEvent("download")
    await page.getByTestId("button-download-pdf").click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/\.pdf$/)
    const stream = await download.createReadStream()
    const chunks: Buffer[] = []
    for await (const chunk of stream) chunks.push(chunk as Buffer)
    const pdf = Buffer.concat(chunks)
    expect(pdf.length).toBeGreaterThan(1000)
    expect(pdf.subarray(0, 5).toString()).toBe("%PDF-")
  })
})
