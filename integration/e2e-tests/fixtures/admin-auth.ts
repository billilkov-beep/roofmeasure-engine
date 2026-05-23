// Shared admin authentication helper. Logs in once and saves the session
// cookie to a file that other tests can reuse via Playwright's `storageState`.

import { Page } from "@playwright/test";
import { ADMIN_EMAIL, ADMIN_PASSWORD } from "./test-data";

export async function loginAsAdmin(page: Page) {
  await page.goto("/admin/login");
  await page.locator('input[name="email"]').fill(ADMIN_EMAIL);
  await page.locator('input[name="password"]').fill(ADMIN_PASSWORD);
  await Promise.all([
    page.waitForURL(/\/admin($|\/)/, { timeout: 15_000 }),
    page.getByRole("button", { name: /login to admin/i }).click(),
  ]);
}
