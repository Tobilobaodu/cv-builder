import { test, expect } from "@playwright/test"

// Sprint 1 e2e: register -> login -> lands on an authenticated dashboard shell,
// against the real backend (docker compose stack on localhost:8000).
test("register, log in, and land on the dashboard shell", async ({ page }) => {
  const email = `e2e-sprint1-${Date.now()}@test.com`
  const password = "Sprint1TestPass123!"

  await page.goto("/register")
  await page.getByLabel("Email").fill(email)
  await page.getByLabel("Password", { exact: true }).fill(password)
  await page.getByLabel("Confirm password").fill(password)
  await page.getByRole("button", { name: "Create account" }).click()

  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 15_000 })
  await expect(page.getByText(`Signed in as ${email}.`)).toBeVisible()

  // Log out and log back in to exercise the /login path independently.
  // Keyboard-driven (Radix's built-in typeahead) rather than a mouse click —
  // the portal-rendered dropdown's on-screen position isn't what's under
  // test here, just that selecting "Log out" actually logs the user out.
  // Logging out from a /dashboard page lands on /login (the route guard's
  // own redirect fires the instant the access token clears) rather than /.
  await page.getByRole("button", { name: email }).click()
  await page.getByRole("menu").waitFor({ state: "visible" })
  await page.getByRole("menuitem", { name: "Log out" }).waitFor({ state: "attached" })
  await page.keyboard.type("Log out", { delay: 50 })
  await page.keyboard.press("Enter")
  await expect(page).toHaveURL(/\/login$/)

  await page.goto("/login")
  await page.getByLabel("Email").fill(email)
  await page.getByLabel("Password").fill(password)
  await page.getByRole("button", { name: "Log in" }).click()

  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 15_000 })
  await expect(page.getByText(`Signed in as ${email}.`)).toBeVisible()
})

test("unauthenticated visitors are redirected away from /dashboard", async ({ page }) => {
  await page.goto("/dashboard")
  await expect(page).toHaveURL(/\/login$/)
})
