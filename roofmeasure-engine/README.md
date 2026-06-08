# roofmeasure_engine

Free-data roof measurement engine for the Canada's Roofer / RoofMeasure Next.js app.

Replaces the hash-based heuristic in `lib/estimate-engine.ts` with an actual
LiDAR + building-footprint pipeline. Geocodes the address, pulls the building
polygon from OpenStreetMap (with Microsoft Building Footprints as fallback),
fetches LiDAR from USGS 3DEP or OpenTopography, runs RANSAC plane segmentation
to extract roof facets, classifies edges as ridge / hip / valley / eave from
geometry, and emits the same `PreliminaryMeasurement` JSON shape your existing
report layer expects.

## Why this exists

The Next.js codebase ships with a `generatePreliminaryMeasurement()` that
**fabricates plausible numbers from `stableHash(address)` plus two hardcoded
sample addresses**. There is no actual measurement happening. For an
insurance-grade or contractor-grade report, that's not acceptable. This engine
replaces the hash with a real LiDAR pipeline while keeping the heuristic as a
fallback for addresses with no LiDAR coverage.

## What you get

For each address, the engine returns:

- Total roof area (sq ft), pitch-corrected from LiDAR
- Roofing squares (3D, EagleView-style rounding)
- Per-facet breakdown: area, pitch (rise/12), azimuth (compass), centroid
- Line measurements: ridges, hips, valleys, eaves (linear ft)
- Obstructions: vents, chimneys, HVAC, skylights (from LiDAR residuals)
- Pitch breakdown by area
- Cost estimate (low/high) using your tenant pricing
- Confidence score driven by LiDAR density + facet count + pipeline notes
- Disclaimer text + data source provenance for the customer-facing report

## Architecture

```
   address
     |
     v
   [ geocode.py ]     Google / Census / Nominatim (free, with key fallback)
     |
     v  lat/lon
   [ footprint.py ]   Overpass (OSM) -> Microsoft Building Footprints fallback
     |
     v  building polygon (WGS-84)
   [ lidar.py ]       OpenTopography (LAZ) -> USGS 3DEP TNM tiles -> crop
     |
     v  point cloud (local meter grid)
   [ segmentation.py ]  RANSAC plane fits -> facets, edges, geometry
     |
     v  facets + edges
   [ obstructions.py ]  LiDAR residual clustering -> vents/chimneys/HVAC
     |
     v  obstruction list
   [ measurement.py ]   Roll up to EagleView-style PreliminaryMeasurement
     |
     v  JSON
   [ measure.py CLI / HTTP server ]   Called by Next.js via measurement-client.ts
```

## Repository layout

```
roofmeasure_engine/
  measure.py                            # CLI + HTTP server entry
  roofmeasure/
    __init__.py
    geocode.py                          # address -> lat/lon
    footprint.py                        # lat/lon -> building polygon (OSM/MSBF)
    lidar.py                            # polygon -> point cloud (USGS/OpenTopo)
    segmentation.py                     # point cloud -> facets + edges
    obstructions.py                     # point cloud -> obstructions
    measurement.py                      # orchestrator + output shaping
    providers/
  tests/
    test_segmentation.py                # smoke test against synthetic hip roof
  examples/
    demo_offline.py                     # full pipeline demo, no network needed
  integration/
    measurement-client.ts               # drop into your Next.js app
    estimate-engine.patch.md            # how to wire it in
```

## Quick start

### Offline smoke test (proves the math works)

```bash
cd roofmeasure_engine
python3 tests/test_segmentation.py
```

Generates a synthetic 6/12 hip roof, runs the full segmentation, asserts:
- 4 facets detected
- Total area within 15% of ground truth (currently 0.2%)
- All pitches within 0.6 of 6/12 (currently within 0.1)
- Edge classifier produces 1 ridge + 4 hips + 4 eaves

### Full pipeline demo (offline, no network)

```bash
python3 examples/demo_offline.py
```

Uses an in-memory footprint and synthetic LiDAR to show the complete output
shape. Useful before you spend time on API keys.

### Real measurement (needs network access)

```bash
# Optional: set a Google Geocoding key for best results
export GOOGLE_GEOCODING_API_KEY=...

# Optional: set an OpenTopography key for the easy LiDAR path
# (free, sign up at opentopography.org/developers)
export OPENTOPO_API_KEY=...

# For laspy support (LAZ point cloud reading)
pip install laspy

python3 measure.py "624 Merrill Ave, Bedford, OH"
# OR
python3 measure.py "2323 Territorial Rd, Guthrie, OK"
```

The CLI prints a JSON object identical to the one the HTTP server sends.

### HTTP server (for the Next.js app)

```bash
python3 measure.py --serve 8088
```

Then in your Next.js `.env`:
```
MEASUREMENT_ENGINE_URL=http://127.0.0.1:8088
MEASUREMENT_ENGINE_TIMEOUT_MS=90000
```

The Next.js app calls `POST /measure {"address": "..."}` and gets back the
same JSON the CLI prints. See `integration/measurement-client.ts` for the
TypeScript client and `integration/estimate-engine.patch.md` for the
three-line patch to wire it into your existing `lib/estimate-engine.ts`.

## Free data sources used

| Stage      | US                                              | Canada                                              |
|------------|-------------------------------------------------|-----------------------------------------------------|
| Geocode    | Google (key) / US Census (free) / Nominatim     | Google / Nominatim                                  |
| Footprint  | OSM Overpass / Microsoft Building Footprints    | OSM Overpass / Microsoft Canadian Building Footprints |
| LiDAR      | OpenTopography (key) / USGS 3DEP TNM (free)     | NRCan HRDEM (free, integrate as follow-up)          |
| Aerial     | NAIP (free, follow-up for SAM2 path)            | SWOOP / provincial portals (follow-up)              |

## Accuracy expectations (where this prototype lands)

Tested against a synthetic ground-truth hip roof:

| Metric          | Truth        | Prototype  | Error |
|-----------------|--------------|------------|-------|
| Total area      | 167.7 m^2    | 167.4 m^2  | 0.2%  |
| Pitch (per facet) | 6.00 / 12  | 5.91-6.00  | <1%   |
| Azimuth         | 0,90,180,270 | 0.1, 89.9, 179.8, 269.9 | < 0.5d |
| Ridge edges     | 1            | 1          | exact |
| Hip edges       | 4            | 4          | exact |
| Eave edges      | 4            | 4          | exact |

On real LiDAR (8-10 pts/m^2), expect ~3-5% on total area for typical
suburban homes with clean LiDAR within 3 years, and 5-10% degradation
when LiDAR is older or trees occlude part of the roof.

## What's NOT in the prototype (roadmap)

These need real work before insurance-grade deployment:

1. **Alpha-shape outlining** instead of convex hull for facet outlines.
   Convex hull currently inflates trapezoidal facet areas by ~5%. Use
   alpha-shape or a Delaunay-triangulation-based approach.

2. **NRCan HRDEM Canadian fallback**. The framework is in place but the
   actual fetcher needs to be implemented. Without this, rural Canadian
   addresses fall back to OpenTopography (US-centric).

3. **SAM2 imagery overlay**. Stub exists in `obstructions.py`. The LiDAR
   residual detector finds raised obstructions well but misses flush
   skylights. SAM2 on a NAIP/Bing aerial tile handles that.

4. **Rake vs eave classifier**. Currently all boundary edges are tagged
   `eave`. Rakes (the sloped boundary edges along gable ends) need a
   geometry test that looks at z-variation along each boundary segment.

5. **Material classifier**. Asphalt shingle vs. metal vs. tile vs. flat
   membrane. Needs a small CNN fine-tuned on aerial tiles - moderate
   training effort.

6. **Multi-building handling**. Currently picks the building containing
   the geocoded point. Detached garages, sheds, etc. would need a
   structure selection step like your existing `StructureSelection` type.

7. **Performance**. RANSAC + edge classification on a typical 50k-point
   roof crop runs in ~3-5 seconds on a single core. Production should
   parallelize across requests (Gunicorn workers) and consider a kd-tree
   or scipy.spatial for neighbour queries instead of the numpy bucket hash.

8. **QA review UI**. The accuracy section above describes synthetic
   ground-truth results. Real production accuracy needs a human-in-the-loop
   QA queue for the engine's lowest-confidence reports (those with
   confidence < 75). This belongs in your existing admin dashboard
   (`app/admin/orders/[orderId]/page.tsx`).

## License & attribution

Code in this repo is provided as a working prototype for canadasroofer.com.
The data sources have their own terms:

- USGS 3DEP: US public domain
- OSM: ODbL (must attribute "(c) OpenStreetMap contributors" on the report)
- Microsoft Building Footprints: ODbL
- OpenTopography: see opentopography.org/usage
- Census Geocoder: US public domain
- Google Geocoding: requires paid API key, has terms of service

Add the OSM attribution to your customer-facing report:
```
"Building footprint data (c) OpenStreetMap contributors."
```

---

## v0.2 additions

This bundle adds:

- **Google Solar API adapter** (`roofmeasure/providers/google_solar.py`) — a paid
  fallback for addresses where free LiDAR is missing or stale. ~$0.50/req.
- **Engine strategies** (`ROOFMEASURE_STRATEGY`): `auto` | `lidar_only` |
  `solar_only` | `solar_first`. Auto = LiDAR primary, Solar fallback.
- **API-key auth** on `/measure` via `X-API-Key` header. Set `ROOFMEASURE_API_KEY`
  env var to enable. `/health` stays unauthenticated for monitoring.
- **Production deployment** — `deploy/` folder with:
  - `nginx.conf` (TLS + rate limit + API key map + defense-in-depth)
  - `lidar_api_keys.map.example` (key allowlist for nginx)
  - `roofmeasure-engine.service` (systemd unit with auto-restart and hardening)
  - `roofmeasure-engine.env.example` (env file template)
  - `DEPLOYMENT.md` (step-by-step Ubuntu deploy guide w/ certbot)
- **Updated TypeScript client** (`integration/measurement-client.ts`) — reads
  the `LIDAR_WORKER_*` env vars from your existing `.env`, sends `X-API-Key`
  header automatically, gates on `ENABLE_LIDAR_WORKER=true`.

## v0.2 testing summary

| Test                                           | Result                              |
|------------------------------------------------|-------------------------------------|
| Segmentation smoke test (hip roof, 0.2% area error) | PASS                          |
| Google Solar adapter on realistic fixture       | PASS (4 facets, 6/12, $9,553 cost)  |
| Offline orchestrator demo                       | PASS (1895 sqft total roof)         |
| Auth gate: /health no auth                      | 200 OK                              |
| Auth gate: /measure missing key                 | 401                                 |
| Auth gate: /measure wrong key                   | 401                                 |
| Auth gate: /measure correct key                 | passes to engine logic              |
| Unknown endpoint                                | 404                                 |

All testable offline. Real-network paths (geocode, OSM, USGS, Solar API) require
running on a machine with internet access — they're wired but not unit-tested.

---

## v0.4 additions

This bundle adds:

- **Usage logging (SQLite)** — every measurement logs an `engine_calls` row
  with timestamp, address hash (PII-safe), strategy, engine source, success,
  latency, cost cents, roof area, confidence. Stored in
  `/var/lib/roofmeasure/usage.db`.
- **`/admin/usage` endpoint** — returns aggregated stats: MTD spend, by-engine
  breakdown, daily call counts, recent failures. Gated by `X-Admin-Key`.
- **Next.js cost dashboard** at `/admin/usage` — MTD Solar spend, daily
  stacked bar chart (LiDAR vs Solar), per-engine table, recent failures.
  Inline SVG, no Chart.js dep.
- **Per-estimate regenerate flow** — `POST /api/admin/estimates/[id]/regenerate`
  with `{strategy}` body. Re-runs a single estimate with an explicit engine
  choice and overwrites the stored preliminary. New `RegenerateEstimateButtons`
  component drops into your existing estimate-detail admin page.

## v0.4 testing summary

| Test                                              | Result                              |
|---------------------------------------------------|-------------------------------------|
| Segmentation smoke (still 0.2% area error)        | PASS                                |
| Solar adapter test                                | PASS                                |
| Offline orchestrator demo                         | PASS                                |
| /admin/usage no key                               | 401                                 |
| /admin/usage with key, empty DB                   | 200, empty aggregates               |
| 5 simulated measurements -> /admin/usage          | 200, 5 calls, avg latency 47ms      |
| Strategy switch persists across server restart    | PASS                                |

## Roadmap (post v0.4)

- Budget alert: env-configured monthly cap that, when crossed, automatically
  flips strategy to `lidar_only` and emails admins
- Per-tenant strategy override (currently global)
- Csv export of `/admin/usage` for accounting
- Alpha-shape outlining (still on the original v0.1 roadmap; would close the
  remaining 5% area-inflation gap)

---

## v0.5 additions

- **Playwright e2e suite** at `integration/e2e-tests/`. Five spec files:
  - `customer-flow.spec.ts` — full happy path with PDF magic-byte verification
  - `customer-validation.spec.ts` — input validation, bad inputs handled cleanly
  - `admin-strategy.spec.ts` — strategy switcher with restore-to-auto cleanup
  - `admin-usage.spec.ts` — cost dashboard renders + usage API schema check
  - `admin-regenerate.spec.ts` — per-estimate regenerate flow with auth checks
- **GitHub Actions workflow** for nightly + on-PR e2e runs against staging
  or production. Trace artifacts retained 14 days on failure, optional Slack
  notify.

## v0.5 testing summary

All previous regression tests still green. New tests are syntax-validated
against node, JSON, and YAML parsers (cannot execute Playwright in this
sandbox; intended to run against your deployed Next.js app).

---

## v0.6 additions

- **`roofmeasure/accessories.py`** — module that fills in line items LiDAR can't
  directly measure: rakes (from boundary geometry), flashing & step flashing
  (formula-based, scales with sqrt(roof area) + chimney count), gutters
  (= eaves length), downspouts (1 per 30 ft of gutter, min 2).
- **Material take-off** — every measurement now includes `accessoryTakeoff[]`
  with: shingle squares + bundles, synthetic underlayment rolls, ice & water
  shield rolls, ridge cap material + bundles, starter strip + bundles, drip
  edge total + 10-ft pieces, roof nails (lbs), pipe boots / vent collars.
- Every line item is tagged `measured | estimated | derived` so the customer
  can see what came from LiDAR geometry vs. a formula.
- **`obstructionSummary`** — count of vents/chimneys/HVAC/skylights detected,
  so the report can render a one-line "Roof features: 3 vents, 1 chimney".

## v0.6 sample output

For the 1895 sqft synthetic hip roof:

| Item                          | Value      | Source     |
|-------------------------------|------------|------------|
| Roof surface area             | 1895.0 sqft| measured   |
| Shingle squares               | 21.33      | derived    |
| Shingle bundles               | 64         | derived    |
| Synthetic underlayment rolls  | 6          | derived    |
| Ice & water shield rolls      | 7          | derived    |
| Ridge cap material            | 125.6 ft   | measured   |
| Ridge cap bundles             | 7          | derived    |
| Starter strip                 | 130.3 ft   | measured   |
| Drip edge pieces (10 ft each) | 14         | derived    |
| Wall flashing                 | 13.1 ft    | estimated  |
| Step flashing                 | 6.5 ft     | estimated  |
| Gutters                       | 130.3 ft   | derived    |
| Downspouts                    | 5          | estimated  |
| Pipe boots / vent collars     | 0          | derived    |
| Roof nails                    | 53.3 lbs   | derived    |

## What v0.6 still does NOT cover

- Gutter shape (K-style vs half-round) — not detectable from LiDAR alone
- Downspout placement — formula only counts, doesn't pick locations
- Specific flashing kind (chimney saddle, headwall, sidewall) — formula lumps them
- Material identification (asphalt vs metal vs tile) — still needs CV pass

The flashing / step-flashing / downspout estimates are explicitly labeled
"estimated" in the report so customers and admins can see they're formula-based.
