# Architecture Decisions — `roofmeasure-engine`

Engine-side ADRs. Portal-side decisions live in
`roofmeasure-portal/docs/DECISIONS.md`.

Each entry: **what we decided, when, why, and what we explicitly rejected.**

---

## 1. No EagleView in the pipeline (foundational)

**Decided:** start of project.

**Why:** the whole product premise is replacing EagleView's per-report cost
with our flat-fee subscription. If we silently call EagleView when our own
algorithms fail, our marginal cost goes up linearly with usage and the
unit economics break.

**Rejected:** the "graceful degradation" approach of falling through to
EagleView as a last resort. Better to return a clearly-labeled
low-confidence result and let the portal decide whether to surface a
warning or refund the credit.

---

## 2. Hybrid LIDAR + Solar API, LIDAR primary

**Decided:** when both are available, prefer LIDAR.

**Why:** LIDAR (USGS 3DEP / NRCan HRDEM) gives us raw 3D points which our
RANSAC segmentation can re-process if we tweak parameters. Solar API gives
us pre-computed facets that we can't refine.

**Rejected:** Solar-only. Coverage is patchy outside major US metros; many
addresses return 404 even at LOW quality. Also Solar's facet count is
typically too coarse vs EagleView ground truth.

---

## 3. v3.4 footprint-area override

**Decided:** when LIDAR segmentation produces a total area that disagrees
with the OSM footprint area by >30%, use
`OSM_area × overhang_factor / cos(pitch)` as the authoritative total.

**Why:** LIDAR plane segmentation can grossly under-count when the point
cloud has gaps, but the OSM footprint is a hard physical truth. This
single override took mean error from ~25% to ~5% on the ground-truth set.

**Rejected:** trusting LIDAR area unconditionally. Tested on Bedford:
LIDAR said 60% of EagleView; OSM footprint said 95%. OSM was right.

---

## 4. v3.5 adaptive overhang factor

**Decided:** overhang multiplier varies by footprint vertex count —
- ≤5 verts: 1.40
- ≤7 verts: 1.25
- ≤12 verts: 1.15
- else:    1.10

**Why:** simple rectangular footprints (low vertex count) imply ranch-style
houses with proportionally larger overhangs vs the footprint. Complex
polygons (many verts) already trace the eave line tightly.

**Rejected:** single global overhang factor. Tested 1.15, 1.20, 1.25 —
none of them got both Cleburne and Bedford into Good simultaneously.

---

## 5. Multi-provider footprint chain order

**Decided:** OSMnx → Overpass → MS Global ML → MS CA legacy → MS US legacy.

**Why:** OSM is the highest-quality where it exists (rich tagging,
community-vetted), Overpass is a direct lower-level fallback for the same
data, MS Global ML covers what OSM misses, and the legacy MS datasets are
last-resort fallbacks.

**Rejected:**
- Google Places `searchNearby` for buildings — returns POIs, not footprints.
- Mapbox Buildings — paid, redundant with MS Global ML.
- Crowdsourced parcel data — too inconsistent across jurisdictions.

---

## 6. NRCan HRDEM via WCS 1.1.1 (not 2.0.1)

**Decided:** Canadian DEM source is NRCan HRDEM mosaic, queried via
WCS 1.1.1 at
`https://datacube.services.geo.ca/wrapper/ogc/elevation-hrdem-mosaic`.

**Why:** HRDEM is 1m resolution, free, no auth, covers >95% of populated
Canada. The provincial LIDAR products (Ontario LIO, BC LIDAR, etc.) vary
per province and the licensing is heterogeneous. NRCan is the lowest-
friction national fallback.

**Quirks the implementation must honor:**
- Server only speaks WCS 1.1.1 (returns 400 for 2.0.1 calls).
- `BoundingBox` parameter format, not `subset`.
- Format must be `image/geotiff`, not `image/tiff`.
- Coverage identifiers are `dsm` and `dtm` (no `-1m` suffix).
- Original `/ows/elevation` endpoint 308-redirects to
  `/wrapper/ogc/elevation-hrdem-mosaic`.

**Rejected:** per-province LIDAR as the primary Canadian source. Kept as
optional providers but not wired into the main chain because NRCan is
broad enough.

---

## 7. Open3D RANSAC for plane segmentation

**Decided:** `open3d.geometry.PointCloud.segment_plane()` iteratively with
normal-direction clustering.

**Why:** open-source, fast, handles noise well, C++ backend with Python
bindings.

**Rejected:**
- scikit-learn RANSAC — too slow for point clouds >100k points.
- PCL via Python bindings — installation pain on Ubuntu 22.04.
- Custom RANSAC — pointless when open3d works.

---

## 8. systemd + nginx (no Docker)

**Decided:** engine runs as a plain systemd service behind nginx.

**Why:**
- Easier to debug (no container layer between us and `journalctl`).
- LIDAR cache on disk persists across deploys without volume gymnastics.
- Hostinger VPS has no registry; Docker would add CI complexity.

**Rejected:** Docker + docker-compose. Tested in early dev; LAZ processing
was 2x slower in container and the disk-cache pattern got awkward.

---

## 9. SQLite for usage logging (no Postgres)

**Decided:** usage logs in SQLite file at `/var/lib/roofmeasure/usage.db`.

**Why:** single-tenant per VPS, data volume <10k rows/month, no concurrent
writers. Hostinger VPS has flaky Postgres support.

**Rejected:** Postgres / managed DB. Re-evaluate when we exceed 100k
estimates/month or move to multi-region.

---

## 10. Engine is stateless from a customer perspective

**Decided:** engine doesn't know who's calling. The X-API-Key authenticates
the *portal*, not an end customer. Per-customer quotas, billing, and
identity all live in the portal.

**Why:**
- Keeps the engine's API surface tiny.
- Portal already needs to track customers for Stripe; no point duplicating.
- Lets us swap portals (or add a second portal product) without changing
  the engine.

**Trade-off:** the engine can't enforce quotas itself. If a malicious
portal floods the engine, we have to rate-limit at nginx and revoke the
shared API key.

---

## 11. Auto-test watcher via inotifywait (no CI for engine harness)

**Decided:** on the VPS, a systemd service runs `inotifywait` on the
engine package directory. Any `.py` edit re-triggers the ground-truth
harness within seconds. Result logged to `/tmp/auto_test_watch.log`.

**Why:** the harness takes 3.5 min. Pushing to GitHub and waiting for CI
is a 10+ minute feedback loop. The local watcher closes the loop to ~4
min and keeps the engineer in flow.

**Rejected:** running the harness in GitHub Actions on every push. Would
require shipping all 11 LAZ fixtures to CI (~500 MB) and running open3d
in a container, which we couldn't get reliable on GH free-tier runners.

---

## 12. Idempotent in-place patches with marker comments

**Decided:** the engine codebase carries a long tail of small algorithmic
patches (v3.1 through v3.12 and counting). Each patch is applied in-place
to the relevant file with a unique marker comment like
`# OSM_SANITY_V312_INLINE` or `# MS_GLOBAL_ML_WIRED`. The deploy script
greps for the marker to know if a patch is already applied.

**Why:** lets `MASTER_DEPLOY.sh` be re-runnable safely. An engineer can
paste the deploy script as many times as they want without worrying about
double-applying patches.

**Rejected:** a proper migration system. Overkill for the volume of patches
we ship and the single-VPS deployment surface.

---

## 13. Single-version v3.x line; no v4.0 cutover

**Decided:** continue iterating in the v3.x line (v3.13, v3.14, …) rather
than declaring a v4.0 and rewriting.

**Why:** the algorithm is converging. Each v3.x patch is small (10-50 lines
of changed code) and validated by the ground-truth harness. A v4.0
rewrite would lose the calibration we have.

**Re-evaluate:** if we ever swap LIDAR segmentation backend (e.g., to a
learned model), that's a v4.0 boundary.
