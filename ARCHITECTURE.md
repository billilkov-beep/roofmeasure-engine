# Architecture — `roofmeasure-engine`

## Where this fits in the product

```
┌────────────────────────────┐     HTTPS + X-API-Key      ┌──────────────────────────────┐
│ roofmeasure-portal         │ ─────────────────────────▶ │ roofmeasure-engine (this)    │
│ Next.js 14, App Router     │  POST /v1/measure          │ FastAPI on Python 3.10       │
│ Hostinger Node hosting     │  { lat, lon, address, … }  │ Hostinger VPS                │
│ Per-tenant white-label     │ ◀─── JSON measurement ──── │ roofmeasure.canadasroofer.com│
└────────────────────────────┘                            └──────────────────────────────┘
                                                                       │
                                                                       ▼ External APIs
                                                          ┌──────────────────────────────┐
                                                          │ OSMnx + Overpass             │
                                                          │ Microsoft Planetary Computer │
                                                          │ USGS 3DEP LIDAR (LAZ)        │
                                                          │ NRCan HRDEM (WCS 1.1.1)      │
                                                          │ Google Solar API             │
                                                          │ Google Static Maps           │
                                                          │ Google Street View           │
                                                          └──────────────────────────────┘
```

The portal owns geocoding, billing, PDF rendering, white-label branding,
and the customer UI. The engine owns everything below the API line —
footprint resolution, LIDAR processing, plane segmentation, edge
classification, accessory formulas, and imagery URLs.

## Tech stack

- **Language:** Python 3.10
- **Web framework:** FastAPI + uvicorn (2 workers, behind nginx)
- **Geometry:** `shapely`, `geopandas`, `pyproj`
- **Plane segmentation:** `open3d` (RANSAC)
- **OSM:** `osmnx` (high-level) + raw `requests` Overpass POST
- **LIDAR:** `laspy` for LAZ I/O, `pdal` for re-projection
- **MS Global ML:** `planetary-computer` + `pystac-client` + `fsspec` + `adlfs`
- **Storage:** SQLite for usage logs, disk cache for LIDAR + MS parquet
- **Process supervision:** systemd
- **Reverse proxy + TLS:** nginx + Let's Encrypt
- **Auto-test:** `inotifywait` watcher → re-runs the ground-truth harness

## Folder map

```
roofmeasure-engine/
├── roofmeasure/                  # Python package
│   ├── __init__.py
│   ├── api/                      # FastAPI app + auth + usage
│   │   ├── main.py              # uvicorn entry point
│   │   ├── routes/
│   │   │   ├── measure.py       # POST /v1/measure
│   │   │   ├── health.py        # GET  /v1/health
│   │   │   └── admin/           # /admin/strategy, /admin/usage
│   │   └── auth.py              # X-API-Key middleware
│   ├── footprint_v2.py           # building polygon resolver
│   ├── lidar_v2_raw.py           # USGS 3DEP LAZ download + crop
│   ├── nrcan_hrdem_provider.py   # Canadian DEM WCS 1.1.1
│   ├── ms_global_ml.py           # MS Global ML via PC STAC
│   ├── segmentation_v2.py        # RANSAC + footprint-area override
│   ├── edge_classification.py    # ridge/hip/valley/rake/eave from face pairs
│   ├── hybrid_pipeline.py        # orchestrator (LIDAR primary, Solar fallback)
│   ├── accessories.py            # vents/pipes/skylights formula
│   ├── imagery.py                # Google Static Maps + Street View URLs
│   └── measurement_v2_wireup.py  # legacy adapter
├── tests/
│   ├── ground_truth.csv          # 11 EagleView reports
│   ├── ground_truth_harness.py   # parallel runner (4 workers)
│   ├── unit/                     # fast unit tests
│   └── fixtures/                 # sample LAZ files
├── scripts/                      # diagnostic shell scripts
├── deploy/
│   ├── MASTER_DEPLOY.sh         # idempotent VPS deploy
│   ├── systemd/                 # .service files
│   └── nginx/                   # vhost config
├── requirements.txt
├── pyproject.toml
├── .env.example
├── AGENTS.md
├── PROJECT_BRIEF.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── DEPLOYMENT_HOSTINGER.md
├── docs/
│   ├── DECISIONS.md
│   └── OPEN_QUESTIONS.md
└── .cursor/rules/project.mdc
```

## Request flow (typical)

1. Portal receives an address, calls **Google Geocoding API** → `(lat, lon)`.
2. Portal calls engine `POST /v1/measure` with `(lat, lon)` and the address
   string (for the imagery URLs) + `X-API-Key` header.
3. Engine middleware validates the API key.
4. Engine `hybrid_pipeline.measure_hybrid(lat, lon)` runs:
   1. `footprint_v2.get_building_footprint(lat, lon)` — multi-provider:
      OSMnx (80 → 150 → 300 → 500 → 1000 m radii) → Overpass POST →
      MS Global ML (Planetary Computer STAC) → MS CA legacy → MS US legacy.
      Returns `BuildingFootprint(polygon_lonlat, source)` or `None`.
   2. If footprint exists, fetch LIDAR:
      - **US:** `lidar_v2_raw.fetch_usgs_3dep(lat, lon)` — TNM Access API to
        find LAZ tile → download from rockyweb → clip to polygon.
      - **Canada:** `nrcan_hrdem_provider.fetch_dem(lat, lon)` — WCS 1.1.1
        GetCoverage on `dsm` / `dtm` mosaic → GeoTIFF → point sample.
   3. Run `segmentation_v2.segment(points, polygon)` — open3d RANSAC,
      per-plane normal clustering, post-merge tiny facets. If the LIDAR
      yields an area that disagrees with the footprint by >30%, apply the
      **v3.4 footprint-area override**: `total = footprint_area × overhang
      / cos(weighted_pitch)`. Overhang factor adapts to vertex count (v3.5).
   4. If LIDAR is missing or segmentation produces 0 facets, call
      `google_solar_api(lat, lon, quality=HIGH)` → if rejected, fall through
      to MEDIUM → LOW (v3.12 quality fallback).
   5. Run `edge_classification.classify(facets)` — ridge/hip/valley/rake/eave
      from per-edge face-pair angles.
   6. Run `accessories.estimate(facets, area)` — vents = ⌈area / 300⌉,
      pipes = vents/2, skylights = formula.
   7. Build imagery URLs via `imagery.get_urls(lat, lon, address)`.
5. Return measurement JSON to portal.

## The fallback ladders

### Footprint (in `footprint_v2.get_building_footprint`)

```
OSMnx 80m → 150m → 300m → 500m → 1000m
    → Overpass POST (3 mirrors)
    → MS Global ML (Planetary Computer STAC)
    → if Canada: MS CA legacy (Ontario only)
    → if US:     MS US legacy (state-routed GeoJSON, currently broken*)
    → None
```

*The legacy US blob URL is dead (HTTP 409). MS Global ML is the working
US fallback. The legacy CA URL still works for Ontario.

### LIDAR (in `hybrid_pipeline.measure_hybrid`)

```
USGS 3DEP (US) OR NRCan HRDEM (CA)
    → Google Solar API (HIGH)
    → Google Solar API (MEDIUM)
    → Google Solar API (LOW)
    → fail with confidence=0
```

### Solar quality gate (in `hybrid_pipeline`)

Even after Solar returns 200, we check the total area against the footprint
area. If Solar returns < 50% of expected area, we reject and fall through
to the next quality level. This catches Solar's silent under-counts.

## Auth + identity

- **Portal ↔ Engine:** shared secret in `X-API-Key` header. The engine
  middleware rejects requests without it. No JWT, no OAuth.
- **No customer identity at the engine layer.** The engine is stateless
  from a customer perspective; it doesn't know who's calling. The portal
  enforces per-customer quotas.

## Data persistence

- **SQLite at `/var/lib/roofmeasure/usage.db`** — every external API call
  logged with provider, status code, latency, $/call estimate. Drives the
  admin cost dashboard (which lives in the portal).
- **Disk cache at `/var/cache/roofmeasure/lidar/`** — LAZ tiles by tile ID,
  TTL 30 days (LIDAR doesn't change).
- **Disk cache at `/var/cache/roofmeasure/ms-buildings/`** — MS Global ML
  parquet partitions by quadkey.
- **No external DB.** Hostinger VPS doesn't reliably support Postgres
  with the level of effort we want to spend.

## Caching strategy

- OSM Overpass: in-memory LRU 1000 entries.
- MS Global ML parquet: on-disk by quadkey, TTL 30 days.
- LIDAR LAZ: on-disk by tile ID, TTL 30 days.
- Measurement result cache: keyed by `sha256(lat, lon, strategy)`. The
  portal can pass `force_refresh=true` to skip it.

## How the engine connects to `roofmeasure-portal`

The portal is a Next.js 14 app deployed to Hostinger's Node hosting (or
shared hosting in legacy mode). It calls this engine over HTTPS.

**What the portal expects from this engine:**

1. A measurement JSON in the shape documented in `AGENTS.md` section 5.
2. A `/v1/health` endpoint returning `{"ok": true, "version": "3.12"}`.
3. An `/admin/strategy` endpoint (X-API-Key required, plus a separate
   admin token) to swap the runtime strategy.
4. An `/admin/usage` endpoint returning rolled-up usage stats.
5. **Idempotency:** calling `/v1/measure` twice with the same `(lat, lon)`
   returns the cached result by default. The portal toggles this with
   `force_refresh=true`.
6. **Bounded latency:** the engine should return within 60 seconds.
   Anything longer is a bug. Default uvicorn `--timeout-keep-alive 30`
   plus a reasonable per-provider timeout enforces this.

**What this engine expects from `roofmeasure-portal`:**

1. The portal does the geocoding. We never see raw address strings except
   for the imagery URL (which uses Google Static Maps' address parameter
   verbatim).
2. The portal owns quota enforcement. We log usage; the portal decides
   whether to block a request before it reaches us.
3. The portal owns the PDF render. We give it a structured measurement
   JSON; it lays out the report.
4. The portal owns Stripe + per-tenant billing. We are stateless from a
   tenant perspective.
