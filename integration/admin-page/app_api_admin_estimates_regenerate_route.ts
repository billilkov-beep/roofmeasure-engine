// Next.js API route: POST /api/admin/estimates/[estimateId]/regenerate
//
// Place at: app/api/admin/estimates/[estimateId]/regenerate/route.ts
//
// Lets an admin re-run a single estimate's measurement with an explicit strategy
// (auto / lidar_only / solar_only / solar_first), overwriting the stored
// preliminary. Useful when a customer complains about a specific number or
// the engine returned low confidence on a property the admin knows the answer for.

import { NextResponse } from "next/server";
import { requireAdminSession } from "@/lib/admin-auth";
import { fetchMeasurementFromEngine } from "@/lib/measurement-client";
import {
  getEstimate,
  updateEstimate,
  getTenant,
} from "@/lib/store";
import {
  calculateConfidence,
  generatePreliminaryMeasurement,
} from "@/lib/estimate-engine";

const VALID_STRATEGIES = ["auto", "lidar_only", "solar_only", "solar_first"] as const;
type Strategy = (typeof VALID_STRATEGIES)[number];

type Ctx = { params: Promise<{ estimateId: string }> };

export async function POST(request: Request, context: Ctx) {
  const session = await requireAdminSession();
  const { estimateId } = await context.params;

  const estimate = await getEstimate(estimateId);
  if (!estimate) {
    return NextResponse.json({ error: "estimate not found" }, { status: 404 });
  }

  const body = await request.json().catch(() => ({}));
  const strategy = body?.strategy as Strategy | undefined;
  if (!strategy || !(VALID_STRATEGIES as readonly string[]).includes(strategy)) {
    return NextResponse.json(
      { error: "invalid strategy", validStrategies: VALID_STRATEGIES },
      { status: 400 },
    );
  }

  const tenant = await getTenant(estimate.tenantId);
  const structure = estimate.selectedStructure || "house";

  // Try the engine with the explicit strategy override
  let newPreliminary = await fetchMeasurementFromEngine(
    estimate.address,
    structure,
    {
      tenant,
      lat: estimate.lat,
      lng: estimate.lng,
      strategy,
    },
  );

  // If the engine call returned null (unreachable, 401, no coverage),
  // fall back to the hash heuristic so the admin still gets a result.
  let usedFallback = false;
  if (!newPreliminary) {
    usedFallback = true;
    newPreliminary = await generatePreliminaryMeasurement(
      estimate.address,
      structure,
      { tenant, lat: estimate.lat, lng: estimate.lng },
    );
  }

  const confidence = calculateConfidence(estimate.address, structure);

  const updated = await updateEstimate(estimate.id, {
    preliminary: newPreliminary,
    confidence,
    // Don't change status - this is a regeneration, not a new estimate
  });

  // Audit log entry. If you have an existing audit table, write there too.
  console.log(
    "[admin-regenerate] %s by %s strategy=%s fallback=%s",
    estimate.id,
    session?.email ?? "admin",
    strategy,
    usedFallback,
  );

  return NextResponse.json({
    estimate: updated,
    strategy,
    usedFallback,
    regeneratedBy: session?.email ?? "admin",
    regeneratedAt: new Date().toISOString(),
  });
}
