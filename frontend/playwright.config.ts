import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "./e2e",
  // Serial on purpose. The backend rate-limits per client IP and every
  // worker is 127.0.0.1, so parallel workers share one budget: 5 trial
  // sessions/hour, 5 auth requests/minute, 10 uploads/hour. Running these
  // four specs on four workers reliably 429s and the failures look like
  // application bugs (blank registration, upload "failed") rather than
  // limits. fullyParallel stays on for when specs are split across shards.
  fullyParallel: true,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    // Port 3000 is Grafana in the backend's docker-compose.yml — using it
    // here let Playwright's reuseExistingServer silently point at Grafana's
    // login page instead of starting the Next.js dev server.
    baseURL: "http://localhost:3100",
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3100",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
})
