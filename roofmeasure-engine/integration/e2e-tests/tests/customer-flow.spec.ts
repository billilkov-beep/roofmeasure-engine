// Customer happy-path: home -> address entry -> structure -> results -> PDF download.
//
// Tests use the address regression anchor `624 Merrill Ave, Bedford, OH` which is
// hardcoded in lib/estimate-engine.ts to produce a deterministic measurement
// (footprint 2790 sqft, pitch 12/12, 19 facets). This keeps the test stable even
// before you swap in the real LiDAR engine - the same anchor still works.

import { test, expect } from "@playwright/test";
import * as fs from "fs";
import { TEST_ADDRESS, PDF_MAGIC } from "../fixtures/test-data";

test.describe("customer estimate flow", () => {
  test("address -> structure -> results -> PDF download", async ({ page }) => {
    // 1. Land on home page
    await page.goto("/");
    await expect(page).toHaveTitle(/measure|roof/i);

    // 2. Enter the address. The input is rendered by AddressAutocompleteInput
    //    which wraps a regular <input id="property-address" name="address">.
    const addressInput = page.locator('input[name="address"], input#property-address');
    await expect(addressInput).toBeVisible();
    await addressInput.fill(TEST_ADDRESS);

    // 3. Submit the address form
    await Promise.all([
      page.waitForURL(/\/estimate\/.+\/confirm/, { timeout: 30_000 }),
      page.getByRole("button", { name: /estimate my roof/i }).click(),
    ]);

    // We should now be on the confirm page; capture the estimate ID from the URL
    const confirmUrl = page.url();
    const match = confirmUrl.match(/\/estimate\/([^/]+)\/confirm/);
    expect(match, `expected estimate id in URL, got ${confirmUrl}`).toBeTruthy();
    const estimateId = match![1];

    // 4. Pick the "House only" structure (smallest blast radius for the test).
    //    The radios are real <input type="radio" value="house|house_garage|garage">.
    await page.locator('input[type="radio"][value="house"]').check();

    // 5. Submit -> we should land on the results page
    await Promise.all([
      page.waitForURL(new RegExp(`/estimate/${estimateId}/results`), { timeout: 90_000 }),
      page.locator('form.structure-form button[type="submit"]').first().click(),
    ]);

    // 6. Verify the results page rendered the measurement
    await expect(page.getByRole("heading", { name: /roof measurement/i }).first()).toBeVisible();
    await expect(page.getByText(TEST_ADDRESS)).toBeVisible();
    // The metric grid should show "Roof Area" with a number > 0
    const bodyText = await page.locator("body").innerText();
    expect(bodyText).toMatch(/\b\d{3,5}\b/); // at least one 3-5 digit number (sqft)

    // 7. Download the PDF and verify its magic bytes
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 30_000 }),
      page.getByRole("link", { name: /download measurement pdf/i }).click(),
    ]);
    const downloadPath = await download.path();
    expect(downloadPath, "PDF should download to disk").toBeTruthy();
    const head = fs.readFileSync(downloadPath!).slice(0, 4);
    expect(head.equals(PDF_MAGIC),
      `downloaded file isn't a PDF, got bytes ${head.toString("hex")}`).toBe(true);

    // 8. Verify the download was non-trivial (a real PDF, not a 100-byte error page)
    const stats = fs.statSync(downloadPath!);
    expect(stats.size, "PDF should be at least 10 KB").toBeGreaterThan(10_000);
  });

  test("results page contains measurement fields", async ({ page, request }) => {
    // Faster variant: skip the UI form steps and hit the API directly,
    // then load the results page and assert it renders the expected fields.
    const startRes = await request.post("/api/estimate/start", {
      data: { address: TEST_ADDRESS },
    });
    expect(startRes.ok()).toBeTruthy();
    const startData = await startRes.json();
    const estimateId = startData.estimateId;
    expect(estimateId).toBeTruthy();

    const confirmRes = await request.post(`/api/estimate/${estimateId}/confirm`, {
      data: { structure: "house" },
    });
    expect(confirmRes.ok()).toBeTruthy();

    await page.goto(`/estimate/${estimateId}/results`);

    // Anchor fields that the EagleView-style report must show
    await expect(page.getByText(/roof area|roof measurement/i).first()).toBeVisible();
    await expect(page.getByText(/squares|sq ft/i).first()).toBeVisible();
    await expect(page.getByText(/pitch/i).first()).toBeVisible();
    await expect(page.getByRole("link", { name: /download measurement pdf/i })).toBeVisible();
  });
});
