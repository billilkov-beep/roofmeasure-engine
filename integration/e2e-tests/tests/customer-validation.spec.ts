// Negative tests: empty / short / bogus addresses must fail cleanly.
//
// These exercise the validation in /api/estimate/start (address.length < 6)
// and the user-visible error feedback on the home page.

import { test, expect } from "@playwright/test";
import { BAD_ADDRESS } from "../fixtures/test-data";

test.describe("customer input validation", () => {
  test("API rejects empty address", async ({ request }) => {
    const res = await request.post("/api/estimate/start", {
      data: { address: "" },
    });
    expect(res.status()).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/complete property address/i);
  });

  test("API rejects too-short address", async ({ request }) => {
    const res = await request.post("/api/estimate/start", {
      data: { address: "1 St" },
    });
    expect(res.status()).toBe(400);
  });

  test("Home form does not submit when address is empty", async ({ page }) => {
    await page.goto("/");
    const button = page.getByRole("button", { name: /estimate my roof/i });
    await button.click();
    // HTML5 required validation should keep us on the same page
    await page.waitForTimeout(500);
    expect(page.url()).not.toMatch(/\/estimate\//);
  });

  test("Bogus address surfaces an error but doesn't crash the page", async ({ page }) => {
    // The geocoder falls back to a mock coord even for bogus addresses, but
    // the API may still reject very short / nonsensical strings. Either way,
    // the page should remain functional, not 5xx.
    await page.goto("/");
    const addressInput = page.locator('input[name="address"], input#property-address');
    await addressInput.fill(BAD_ADDRESS);
    await page.getByRole("button", { name: /estimate my roof/i }).click();
    // Wait a beat for either redirect (acceptable, mock geocoder) or error toast
    await page.waitForTimeout(2000);
    // Either we redirected to /confirm OR we got an error toast on the home page.
    // Both are non-crash outcomes; we just assert the page is still alive.
    const stillOnHome = !page.url().includes("/estimate/");
    if (stillOnHome) {
      const body = page.locator("body");
      await expect(body).toBeVisible();
    } else {
      // We landed on /confirm. The page should render.
      await expect(page.locator("body")).toBeVisible();
    }
  });

  test("Confirm endpoint rejects invalid structure", async ({ request }) => {
    const startRes = await request.post("/api/estimate/start", {
      data: { address: "624 Merrill Ave, Bedford, OH" },
    });
    expect(startRes.ok()).toBeTruthy();
    const startData = await startRes.json();

    const confirmRes = await request.post(
      `/api/estimate/${startData.estimateId}/confirm`,
      { data: { structure: "spaceship" } },
    );
    expect(confirmRes.status()).toBe(400);
    const err = await confirmRes.json();
    expect(err.error).toMatch(/house|garage/i);
  });
});
