// /admin/usage cost dashboard renders.

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../fixtures/admin-auth";

test.describe("admin: usage dashboard", () => {
  test("dashboard loads and renders metric cards", async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto("/admin/usage");

    await expect(page.getByRole("heading", { name: /usage.*cost/i })).toBeVisible();
    await expect(page.getByText(/month-to-date spend/i)).toBeVisible();
    await expect(page.getByText(/calls this month/i)).toBeVisible();
    await expect(page.getByText(/failed calls/i)).toBeVisible();
    await expect(page.getByText(/lifetime calls/i)).toBeVisible();

    // The "By engine" table is always rendered, even when empty
    await expect(page.getByRole("heading", { name: /by engine/i })).toBeVisible();
  });

  test("usage API responds with valid schema", async ({ page }) => {
    await loginAsAdmin(page);
    const res = await page.request.get("/api/admin/lidar-worker/usage");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data).toHaveProperty("totals");
    expect(data).toHaveProperty("byEngine");
    expect(data).toHaveProperty("byDay");
    expect(data).toHaveProperty("recentFailures");
    expect(data.totals).toHaveProperty("c");
    expect(data.totals).toHaveProperty("cents");
  });
});
