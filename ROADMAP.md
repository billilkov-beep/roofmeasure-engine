# Roadmap — `roofmeasure-engine`

This is the engine's roadmap. The portal's roadmap lives in
`roofmeasure-portal/ROADMAP.md`.

## Completed

### Foundations
- FastAPI service with `X-API-Key` middleware
- Usage logging in SQLite per external-API call
- systemd unit + nginx vhost + Let's Encrypt TLS
- GitHub Actions deploy hooks (engine side)
- Pre-flight provider URL validator

### Footprint resolution
- OSMnx with radius escalation 80m → 150m → 300m → 500m → 1000m (v3.11)
- Direct Overpass POST fallback with multi-mirror retry
- Microsoft Global ML Buildings via Planetary Computer STAC + SAS signing
- Microsoft Canada (Ontario only) GeoJSON fallback
- Union of nearby OSM buildings within 5m (v3.6)
- OSM polygon sanity check (rejects polygons outside [50, 2000] m²)
- Provenance tracked on every returned footprint

### LIDAR + DEM
- USGS 3DEP via TNM Access API + rockyweb LAZ download
- LAZ on-disk cache keyed by tile ID (TTL 30 days)
- LAZ polygon-clip via `lidar_v2_raw.crop_to_polygon`
- NRCan HRDEM Canadian DEM via WCS 1.1.1 (correct endpoint, version,
  identifiers `dsm`/`dtm`, format `image/geotiff`)

### Segmentation + algorithm
- Open3D RANSAC plane segmentation
- v3.1 facet post-merge + sanity filters
- v3.2 XY-union total area calc
- v3.3 point-assignment area calc (reverted in favor of v3.1)
- **v3.4 footprint-area override** (the single highest-impact change)
- **v3.5 adaptive overhang factor** by vertex count
- v3.10 absorb-tiny facets with area redistribution
- v3.11 Solar quality gate (reject Solar if <50% expected area)
- v3.12 Solar HIGH → MEDIUM → LOW fallback chain

### Edges + accessories + imagery
- Edge classification: ridge/hip/valley/rake/eave from face-pair angles
- Accessories formula: vents = ⌈area_sqft / 300⌉, pipes = vents/2,
  skylights = formula based on facet count + area
- Google Static Maps aerial URL builder
- Google Street View hero URL builder

### Testing + ops
- 11 EagleView ground-truth addresses geocoded + ingested
- EagleView PDF parser (`scripts/add_eagleview_pdf.sh`) — US + Canadian
  report formats
- Parallel ground-truth harness (4 workers, 12 min → 3.5 min)
- Auto-test watcher via inotifywait + systemd
- Provider health check script (`scripts/check_providers.sh`)
- LIDAR inspector (`scripts/lidar_inspector.sh`) — point cloud + normals + PNG
- Solar inspector (`scripts/solar_inspector.sh`) — all 3 quality levels
- Address diagnostic (`scripts/address_diagnostic.sh`) — full debug dump
- Engine state inspector (`scripts/show_deployed.sh`)
- Run comparison tool (`scripts/compare_runs.sh`)
- Parameter calibration grid search (`scripts/calibrate.sh`)
- MASTER_DEPLOY.sh — idempotent single-paste VPS deploy

## Partially completed

- **MS Global ML provider** works but downloads entire quadkey parquets
  per query (multi-MB). Needs `pyarrow` predicate pushdown for acceptable
  latency at scale. Bandwidth at 10k queries/day would be ~50 GB.
- **OSM polygon sanity check** is defined but the [50, 2000] m² range is
  too loose for residential. Joyceville's 665 m² polygon passes but is
  geometrically wrong (likely a barn). Needs a smarter
  "closest-polygon-to-geocoded-point with size penalty" picker.
- **Confidence score** is computed internally but not yet exposed in the
  measurement JSON — needs to be added to the response schema.

## Known broken

- **Geocoding (in the portal, but root cause of engine failures)** —
  Nominatim returns road-centroid coordinates for rural addresses,
  causing the engine's polygon picker to grab the wrong building. This
  is the highest-impact open bug. The fix is in `roofmeasure-portal`,
  not here, but the engine should add defensive handling.
- **Engine systemd hang after failed LIDAR runs** (task #66) — when a
  LIDAR download times out, the engine worker can become unresponsive
  until restart. Workaround: `systemctl restart roofmeasure-engine`.
  Needs `signal.alarm` + worker recycling.
- **MS US legacy URL is dead** (HTTP 409 "Public access not permitted").
  Already removed from the active chain; MS Global ML covers it.
- **Solar 404 at HIGH quality on some Canadian addresses** — fallback to
  MED then LOW works, but worth understanding why HIGH fails.

## Ground-truth results (most recent run)

| # | Address | Source | Δ% | Bucket |
|---|---|---|---|---|
| 1 | Cleburne TX | lidar | +6.5% | Good |
| 2 | Oklahoma City OK | lidar | -3.3% | Excellent |
| 3 | Edmond OK | **fail** | — | — |
| 4 | Guthrie OK | **fail** | — | — |
| 5 | Bedford TX | lidar | -6.1% | Good |
| 6 | Nanaimo BC (Chelsea) | lidar | -9.8% | Good |
| 7 | South Frontenac ON | lidar | -28.9% | **Poor** |
| 8 | Greater Napanee ON | lidar | -5.2% | Excellent |
| 9 | Joyceville ON | lidar | +221% | **Very Poor** |
| 10 | Tweed ON | **fail** | — | — |
| 11 | Nanaimo BC (Arbutus) | **fail** | — | — |

**Summary: 7/11 succeeded. 5 in Excellent/Good. 2 Poor. 4 failed.**

## Next 10 development tasks (priority order)

1. **Smart polygon picker.** When `_pick_best_polygon` has multiple
   candidates within search radius, score them by combined
   `(distance_to_geocoded_point, area_within_residential_range,
   shape_compactness)` rather than picking the nearest. Lives in
   `footprint_v2.py`. Likely fixes Joyceville and Frontenac.

2. **Defensive geocode validation in the engine.** Even though the portal
   does geocoding, the engine should reject obviously-bad coordinates
   (e.g., point falls in middle of road centerline without any building
   within 50m) and return `success=false, reason="geocoding_off_target"`
   so the portal can surface a useful error.

3. **MS Global ML parquet predicate pushdown.** Replace
   `geopandas.read_parquet(href, storage_options=so)` with
   `pyarrow.parquet.read_table(href, filters=[…])` using the geometry
   bbox. Should drop per-query bandwidth from ~5 MB to ~50 KB.

4. **Engine systemd hang fix** (task #66). Wrap every external HTTP
   call in `signal.alarm`-based timeout-with-kill. Add worker recycling
   in the uvicorn config (`--limit-max-requests 1000`).

5. **Confidence score in response JSON.** Compute from agreement
   between sources (LIDAR vs Solar), polygon sanity score, and Solar
   quality level. Range 0.0-1.0. Add to `MeasurementResult` dataclass
   and the `/v1/measure` response.

6. **Rotate Google API key + LIDAR worker keys** (task #67). The
   current Google key was exposed in chat history. Rotate via Google
   Cloud Console → update `GOOGLE_API_KEY` in `.env` on VPS → restart
   service.

7. **Address geocoding cache via Redis or SQLite.** Even though the
   portal does the geocoding, the engine could cache `(address →
   lat,lon)` to dedupe portal-side Google calls. Optional; only worth
   it if portal usage is high.

8. **Expand ground truth from 11 → 50 addresses.** Most failure modes
   cluster on rural addresses; we need more rural samples to validate
   fixes. Use `scripts/add_eagleview_pdf.sh` to ingest PDFs one at a
   time.

9. **Per-tenant API key scoping.** Today all portals share one engine
   API key. When we have multiple tenants on the portal, give each its
   own key so we can rate-limit and revoke per-tenant.

10. **Background pre-warm of MS Global ML parquets.** A nightly cron
    job that downloads MS parquets for US states + Canadian provinces
    where our roofers have customers. Eliminates the cold-start
    latency for the first request in each quadkey.
