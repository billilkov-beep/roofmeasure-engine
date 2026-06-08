// Exercises the strategy switcher: /admin/lidar-worker -> click solar_only ->
// verify it sticks, then flip back to auto. Requires the engine to be running
// AND ROOFMEASURE_ADMIN_KEY / LIDAR_WORKER_ADMIN_KEY to be set on both sides.

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../fixtures/admin-auth";

test.describe("admin: LiDAR worker strategy switcher", () => {
  test("switch strategy via the UI", async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto("/admin/lidar-worker");

    // The page should render the current strategy + the four buttons
    await expect(page.getByRole("heading", { name: /lidar worker strategy/i })).toBeVisible();
    await expect(page.getByText(/current strategy/i)).toBeVisible();

    // Read the initial strategy off the page so we can restore it at the end
    const initialStrategy = (await page
      .locator('div:has-text("Current strategy") + div, [aria-label="current-strategy"]')
      .first()
      .textContent({ timeout: 2_000 })
      .catch(() => null)) || "auto";

    // Flip to solar_only
    await page.getByRole("button", { name: /solar api only/i }).click();
    // Page refreshes; the active button should now reflect solar_only
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/solar_only/i).first()).toBeVisible({ timeout: 10_000 });

    // Flip back to auto so we don't leave production in a non-default state
    await page.getByRole("button", { name: /^auto/i }).click();
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/^auto$/i).first()).toBeVisible({ timeout: 10_000 });
  });

  test("API rejects invalid strategy", async ({ request, page }) => {
    await loginAsAdmin(page);
    // The session cookie is now in the request context for this browser
    const res = await page.request.post("/api/admin/lidar-worker/strategy", {
      data: { strategy: "bogus" },
    });
    expect(res.status()).toBe(400);
    const err = await res.json();
    expect(err.validStrategies).toContain("auto");
  });

  test("unauthenticated request is rejected", async ({ request }) => {
    // Fresh request context has no admin cookie
    const res = await request.post("/api/admin/lidar-worker/strategy", {
      data: { strategy: "solar_only" },
    });
    expect(res.status()).toBeGreaterThanOrEqual(401);
    expect(res.status()).toBeLessThan(500);
  });
});
