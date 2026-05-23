# Patch to `lib/estimate-engine.ts` (existing Next.js app)

Drop-in changes to wire the LiDAR worker / Solar API engine into your existing
estimate flow. The hash-based heuristic stays as fallback when the worker is
disabled or unreachable.

## 1. Add the new file

Copy `integration/measurement-client.ts` -> `lib/measurement-client.ts` in your
Next.js project.

## 2. Add the import at the top of `lib/estimate-engine.ts`

```ts
import { fetchMeasurementFromEngine } from "./measurement-client";
```

## 3. Change `generatePreliminaryMeasurement` to async + try the worker first

Find:
```ts
export function generatePreliminaryMeasurement(
  address: string,
  structure: StructureSelection,
  options?: { tenant?: Tenant; lat?: number; lng?: number },
): PreliminaryMeasurement {
  const hash = stableHash(...);
```

Replace with:
```ts
export async function generatePreliminaryMeasurement(
  address: string,
  structure: StructureSelection,
  options?: { tenant?: Tenant; lat?: number; lng?: number },
): Promise<PreliminaryMeasurement> {
  // Try the real LiDAR worker first (gated on ENABLE_LIDAR_WORKER env)
  if (process.env.ENABLE_LIDAR_WORKER === "true") {
    const real = await fetchMeasurementFromEngine(address, structure, options);
    if (real) {
      return real;
    }
  }

  // ----- FALLBACK: existing hash-based heuristic -----
  // Triggers when ENABLE_LIDAR_WORKER=false OR the worker returned null
  // (unreachable / 401 / 500 / no LiDAR coverage).
  const hash = stableHash(...);
```

(Keep everything after this line unchanged — the heuristic stays as fallback.)

## 4. Update the two call sites to `await`

### `app/api/estimate/[estimateId]/confirm/route.ts`

Before:
```ts
const preliminary = generatePreliminaryMeasurement(estimate.address, structure, { tenant, lat: estimate.lat, lng: estimate.lng });
```

After:
```ts
const preliminary = await generatePreliminaryMeasurement(estimate.address, structure, { tenant, lat: estimate.lat, lng: estimate.lng });
```

### `lib/estimate-engine.ts` — inside `generateVerifiedMeasurement`

Make it `async` and `await` its internal call:
```ts
export async function generateVerifiedMeasurement(estimate: Estimate, providerOrderId: string): Promise<VerifiedMeasurement> {
  const fallback = await generatePreliminaryMeasurement(estimate.address, ...);
  // ... rest unchanged ...
}
```

Then find anywhere `generateVerifiedMeasurement(...)` is called and add `await`.

## 5. Wire the env vars

Add to `.env` (and `.env.example`):

```
ENABLE_LIDAR_WORKER=true
LIDAR_WORKER_URL=https://lidar-worker.canadasroofer.com
LIDAR_WORKER_API_KEY=<paste-the-key-from-the-server>
LIDAR_WORKER_TIMEOUT_MS=90000

# Optional: force a specific engine on every request (default: server decides)
# auto | lidar_only | solar_only | solar_first
LIDAR_WORKER_STRATEGY=auto
```

The client also accepts the legacy `MEASUREMENT_ENGINE_*` names for backward
compatibility.

## 6. Verify it's working

After deploy, request an estimate and inspect the resulting JSON. Real-engine
output has these tells:

- `sourceSummary` mentions `LiDAR` or `Google Solar API`, not just `Google Maps/geocoding assisted`
- `confidenceScore` varies between 35-98 based on point density
- `dataSources.lidar` is set to `usgs_3dep`, `opentopo`, or `google_solar` (not `synthetic`)
- `facets[]` is non-empty and contains per-facet pitch/azimuth/area

If you see the old hash-style output (`sourceSummary` starts with "Google Maps/geocoding assisted"),
the worker is unreachable - check the Next.js server logs for `[measurement-client]` warnings.
