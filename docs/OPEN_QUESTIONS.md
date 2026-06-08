# Open Questions — `roofmeasure-engine`

Things we know we don't know. Engine-side only — portal-side open
questions live in `roofmeasure-portal/docs/OPEN_QUESTIONS.md`.

Each item is something a future engineer (or the founder) should
research, decide, or answer.

---

## Algorithm

### 1. Why does Joyceville's OSM polygon report 665 m² (passes sanity) but produce a roof area 3x too large?

The polygon is geometrically plausible (5 verts, residential-scale area).
But the resulting roof total is 11,762 sqft vs 3,658 ground truth.

Hypotheses:
- The 665 m² polygon is actually a barn or outbuilding larger than the
  house itself. Need aerial imagery to confirm.
- Multiple OSM buildings exist at this location and `_pick_best_polygon`
  picks the wrong one because the geocoded point is 100+ m off.
- The polygon is the right building but our overhang factor is wrong for
  that house style.

How to investigate: `bash scripts/address_diagnostic.sh "3492 Woodburn Rd
Joyceville ON"` + cross-reference Google aerial imagery.

---

### 2. What's the right approach for "the geocoded point isn't on any building"?

For rural addresses, Nominatim (and even Google Maps in some cases) returns
a road centroid. The current code picks the nearest building, which for
rural properties can be a neighbor's barn 200m away.

Options:
- A. Lean on the portal — make the portal use Google Address Validation API
  (returns rooftop accuracy for >90% of US addresses).
- B. Add defensive logic in the engine: if no building within 50m of
  geocoded point, return `success=false, reason="geocoding_off_target"`.
- C. Use parcel data (Regrid, county GIS) to constrain search to the parcel.

A + B together is probably right. Decide before tackling rural failures.

---

### 3. Should the engine reject low-confidence results?

Today the engine returns whatever it can produce. Some results have
confidence ~0.3 but we still return them with the headline number.

Should we instead:
- Return `success=false` when confidence < 0.5?
- Always return the number but never expose it via the portal API to
  customers (only to roofers)?
- Add a `warnings` array that the portal can render?

---

### 4. Is the +6.5% Cleburne result actually "Excellent"?

We bucket >5% error as "Good." But EagleView themselves quote ±3-5%
accuracy. If +6.5% is within the noise floor of EagleView's own
measurements, calling it "Good" might be too harsh.

Action: ask founder if Excellent should be reset to <8%.

---

### 5. How do we handle multi-building parcels?

Joyceville's EagleView report measures only one building (the house).
But the OSM record may have a single polygon spanning house + attached
garage + breezeway. Or three separate polygons for house + garage + shed.

Current behavior: takes the polygon nearest the geocoded point. Probably
wrong when the geocoded point is between buildings.

---

## Provider integration

### 6. How do we scale the MS Global ML parquet downloads?

A single query downloads multiple multi-MB parquet files. At 100 queries/
day this is ~500 MB/day; at 10k queries/day it's 50 GB. Bandwidth and
latency both unacceptable at scale.

Options:
- pyarrow predicate pushdown with `read_table(href, filters=…)` — fetches
  only relevant row groups (~50 KB per query).
- Pre-download parquets for US + Canadian populated areas — ~30 GB on disk.
- Switch to Overture Maps Buildings, which has spatially-partitioned
  cloud-optimized GeoParquet.

---

### 7. Should we add ESRI World Buildings as a tertiary fallback?

Some rural Canadian addresses (Tweed, Arbutus) fail with no footprint
from any of OSM / Overpass / MS Global ML / MS CA legacy / NRCan.

ESRI publishes a Living Atlas building footprints layer with broader
rural coverage. Free for non-commercial; commercial requires a Business
license. Worth investigating cost.

---

### 8. Why does Google Solar API return 404 at HIGH but 200 at MEDIUM for some Canadian addresses?

Specifically Nanaimo Chelsea returns HIGH 404 but MEDIUM works fine. The
HIGH endpoint is supposed to be a superset of MEDIUM (higher imagery
resolution). The 404 suggests Google's image catalog has incomplete
coverage at HIGH for parts of BC.

Action: ask Google support, or read the actual `imageryQuality` field of
nearby successful HIGH responses to understand the threshold.

---

## Engine ↔ portal contract

### 9. Does the engine need to know the customer/tenant identity for usage attribution?

Today: no. Engine logs go to a shared SQLite; the portal does per-tenant
attribution by joining engine logs with its own request log on timestamp.

Should we add a `tenant_id` field to `/v1/measure` requests so usage
attribution is exact?

Pro: cleaner billing.
Con: more API surface area; harder to revoke per-tenant access.

---

### 10. Should the engine return the input lat/lon back in the response?

Today: no, we just return measurements. But if the portal caches a
request and the engine internally falls through to a slightly different
location (e.g., snapped to nearest building), the portal doesn't know.

Adding `geocode_used: {lat, lon}` to the response would help debugging.

---

### 11. What's the retry contract?

If a portal call to `/v1/measure` times out at 60s, should the portal
retry? Today we don't say. The engine's work is idempotent (same input
→ same cached result), but a retry storm could exhaust LIDAR providers.

Document: engine returns 503 with `Retry-After` when it's overloaded;
portal must respect `Retry-After` and not retry faster.

---

## Operational

### 12. What's the right behavior when LIDAR is available but yields zero facets?

Currently: fall through to Solar. But this masks LIDAR pipeline bugs.

Should the engine instead:
- Log loudly and return an explicit error (so we notice the regression)?
- Try LIDAR with different segmentation parameters before falling through?
- Compare LIDAR's "zero facets" to Solar's facet count and pick the
  larger one?

---

### 13. Do we need an async / queue model?

Today: every request blocks until the engine returns (~6-90 sec). At
production load this saturates the 2-worker uvicorn config.

Options:
- Add a Redis-backed BullMQ-style queue and return a job ID immediately.
- Increase uvicorn workers to 8 and hope it's enough.
- Move to serverless (Lambda + EFS for cache) — but cold starts with
  open3d are slow.

Probably wait until we see actual load patterns.

---

### 14. How do we test the engine's behavior under provider failures?

The auto-test harness runs against real providers. If Overpass goes down,
the harness might falsely flag a regression. We need a "provider
down-simulation" mode that injects 503s on specific providers to verify
the fallback chain.

---

## Business / legal

### 15. Are we allowed to ship EagleView PDFs as test fixtures?

Bill owns the PDFs (he paid EagleView). But shipping them in the repo as
fixtures could violate EagleView's TOS.

Current approach: keep PDFs out of the repo; only the parsed
`ground_truth.csv` ships. Confirm with a lawyer before going commercial.

---

### 16. Are MS Building Footprints suitable for commercial use?

- MS USBuildingFootprints: ODbL-licensed (similar to OSM).
- MS Global ML Buildings: CDLA-Permissive-2.0.

Both allow commercial use but ODbL requires share-alike on derivative
datasets. We're not publishing derived datasets — we use the polygons to
compute measurements. Probably fine, but get a license review before
charging customers.

---

### 17. What's the SLA we're willing to commit to?

Today: best-effort, no SLA. Free tier is fine. But Tier 3 ($299/month
unlimited) is the kind of price where customers expect 99% uptime
guarantees.

What latency and uptime are we willing to back with credits?
