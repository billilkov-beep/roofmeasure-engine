// Per-estimate regenerate flow:
//   1. Create a fresh estimate via the public API
//   2. Log in as admin
//   3. Hit the regenerate endpoint with strategy=lidar_only
//   4. Verify the estimate's preliminary was updated

import { test, expect } from "@playwright/test";
import { loginAsAdmin } from "../fixtures/admin-auth";
import { TEST_ADDRESS } from "../fixtures/test-data";

test.describe("admin: regenerate estimate", () => {
  test("regenerate with lidar_only strategy updates the estimate", async ({ page, request }) => {
    // 1. Create an estimate as a customer
    const start = await request.post("/api/estimate/start", {
      data: { address: TEST_ADDRESS },
    });
    expect(start.ok()).toBeTruthy();
    const { estimateId } = await start.json();

    const confirm = await request.post(`/api/estimate/${estimateId}/confirm`, {
      data: { structure: "house" },
    });
    expect(confirm.ok()).toBeTruthy();
    const before = await confirm.json();

    // 2. Log in as admin (shares cookie with `page.request`)
    await loginAsAdmin(page);

    // 3. Regenerate with lidar_only
    const regenRes = await page.request.post(
      `/api/admin/estimates/${estimateId}/regenerate`,
      { data: { strategy: "lidar_only" } },
    );
    expect(regenRes.ok()).toBeTruthy();
    const regen = await regenRes.json();
    expect(regen.strategy).toBe("lidar_only");
    expect(regen.regeneratedBy).toBeTruthy();
    expect(regen.regeneratedAt).toBeTruthy();
    // The fallback flag tells us whether the engine was reachable
    expect(typeof regen.usedFallback).toBe("boolean");

    // 4. Fetch the estimate again and confirm it was updated
    const afterRes = await request.get(`/api/estimate/${estimateId}`);
    expect(afterRes.ok()).toBeTruthy();
    const after = await afterRes.json();
    expect(after.estimate?.preliminary?.roofAreaSqFt).toBeTruthy();
    // updatedAt should be newer than before regeneration
    expect(new Date(after.estimate.updatedAt).getTime())
      .toBeGreaterThanOrEqual(new Date(before.estimate.updatedAt).getTime());
  });

  test("invalid strategy is rejected with 400", async ({ page, request }) => {
    const start = await request.post("/api/estimate/start", {
      data: { address: TEST_ADDRESS },
    });
    const { estimateId } = await start.json();
    await request.post(`/api/estimate/${estimateId}/confirm`, {
      data: { structure: "house" },
    });

    await loginAsAdmin(page);
    const res = await page.request.post(
      `/api/admin/estimates/${estimateId}/regenerate`,
      { data: { strategy: "bogus" } },
    );
    expect(res.status()).toBe(400);
  });

  test("unauthenticated regenerate is rejected", async ({ request }) => {
    const start = await request.post("/api/estimate/start", {
      data: { address: TEST_ADDRESS },
    });
    const { estimateId } = await start.json();

    const res = await request.post(
      `/api/admin/estimates/${estimateId}/regenerate`,
      { data: { strategy: "lidar_only" } },
    );
    expect(res.status()).toBeGreaterThanOrEqual(401);
    expect(res.status()).toBeLessThan(500);
  });
});
