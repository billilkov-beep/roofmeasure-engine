// Playwright config for canadasroofer.com end-to-end tests.
//
// Run locally:        npx playwright test
// Run against prod:   BASE_URL=https://measure.canadasroofer.com npx playwright test
// Headed (debug):     npx playwright test --headed --project=chromium
// One test only:      npx playwright test customer-flow

import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI
    ? [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]]
    : "list",

  use: {
    baseURL: BASE_URL,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },

  expect: {
    // The engine can take 30-90s on a real LiDAR fetch.
    // Bump default expectation timeout so we don't flake on slow LAZ downloads.
    timeout: 90_000,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    // Uncomment if you want to test multiple browsers in CI:
    // { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    // { name: "webkit",  use: { ...devices["Desktop Safari"]  } },
  ],

  // Optional: spin up the dev server automatically when running locally.
  // For CI / staging tests, point BASE_URL at the deployed URL and remove this block.
  ...(process.env.START_DEV_SERVER === "true" && {
    webServer: {
      command: "npm run dev",
      url: BASE_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  }),
});
