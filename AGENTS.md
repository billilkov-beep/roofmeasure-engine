# AGENTS.md — `roofmeasure-engine`

You are working in **`roofmeasure-engine`**, the Python backend that
computes roof measurements from a lat/lon. This is one of two
repositories in the RoofMeasure product:

| Repo | Role | Lives where |
|---|---|---|
| **`roofmeasure-engine`** (this repo) | FastAPI service: footprint resolution, LIDAR/Solar fusion, plane segmentation, accessory take-off, PDF data generation | Hostinger VPS at `roofmeasure.canadasroofer.com` |
| **`roofmeasure-portal`** | Next.js customer-facing portal + admin dashboard. Calls this engine over HTTPS with an API key | Hostinger shared/Node hosting at per-tenant white-label domains |

This file is the entry point for any AI agent (Cursor primarily) opening
this repo. Read it before touching code.

## 1. Working layout

```
roofmeasure-engine/
├── roofmeasure/               ← Python package (all engine code)
│   ├── api/                  ← FastAPI routes, API-key auth, usage logging
│   ├── footprint_v2.py       ← multi-provider building footprint resolver
│   ├── lidar_v2_raw.py       ← LAZ download + crop (USGS 3DEP)
│   ├── nrcan_hrdem_provider.py ← Canadian DEM via WCS 1.1.1
│   ├── ms_global_ml.py       ← MS Global ML Buildings via PC STAC
│   ├── segmentation_v2.py    ← RANSAC plane seg + footprint-area override
│   ├── edge_classification.py← ridge/hip/valley/rake/eave from face pairs
│   ├── hybrid_pipeline.py    ← orchestrator (LIDAR primary, Solar fallback)
│   ├── accessories.py        ← formula-based vents/pipes/skylights
│   ├── imagery.py            ← Google Static Maps + Street View
│   └── measurement_v2_wireup.py ← legacy adapter for old portal schema
├── tests/
│   ├── ground_truth.csv      ← 11 EagleView reports (5 US, 6 CA)
│   ├── ground_truth_harness.py ← parallel test runner (4 workers)
│   └── fixtures/             ← sample LAZ files for offline tests
├── scripts/                  ← diagnostic shell scripts (quick_test, etc.)
├── deploy/                   ← MASTER_DEPLOY.sh, systemd units, nginx config
├── requirements.txt
├── pyproject.toml
└── AGENTS.md                 ← this file
```

## 2. Setup (local dev)

```bash
# Ubuntu 22.04 / WSL2 / macOS with Homebrew
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
# Core deps include:
#   open3d, osmnx, geopandas, shapely, pyproj, laspy, pdal,
#   requests, fastapi, uvicorn, planetary-computer, pystac-client,
#   fsspec, adlfs, matplotlib, pyarrow

cp .env.example .env
# Fill ENGINE_API_KEY and GOOGLE_API_KEY at minimum (see .env.example).

# Smoke test
bash scripts/quick_test.sh "1404 Wedgewood Dr Cleburne TX 76033"

# Run the API locally
uvicorn roofmeasure.api.main:app --reload --port 8080
```

## 3. Build / test / run

```bash
# Run a single address through the full pipeline (no API server)
bash scripts/quick_test.sh "<address-string>"

# Full ground-truth harness (parallel, ~3.5 min)
python tests/ground_truth_harness.py tests/ground_truth.csv

# Diagnose a single address (OSM polygon, LIDAR points, segmentation, GT delta)
bash scripts/address_diagnostic.sh "<address-string>"

# LIDAR-only deep dive (point cloud, normals, pitch histogram, 3D PNG)
bash scripts/lidar_inspector.sh "<address-string>"

# Solar API deep dive (HIGH/MED/LOW raw JSON + segment breakdown)
bash scripts/solar_inspector.sh "<address-string>"

# Provider health check (TNM, Overpass, Solar, NRCan, PC STAC)
bash scripts/check_providers.sh

# Show which v3.x patches are currently live in the engine files
bash scripts/show_deployed.sh

# Run the FastAPI service in foreground for debugging
ENGINE_API_KEY=test python -m uvicorn roofmeasure.api.main:app --port 8080
```

## 4. Deploy

The engine deploys to a Hostinger VPS. **Read `DEPLOYMENT_HOSTINGER.md`**.
Short version:

```bash
# From your laptop:
scp deploy/MASTER_DEPLOY.sh root@roofmeasure.canadasroofer.com:/tmp/
ssh root@roofmeasure.canadasroofer.com 'bash /tmp/MASTER_DEPLOY.sh'
```

`MASTER_DEPLOY.sh` is idempotent — re-running is safe. It backs up current
state, applies the latest patches, runs the harness, and prints results.

## 5. The API contract with `roofmeasure-portal`

The portal sends requests like this:

```http
POST /v1/measure HTTP/1.1
Host: roofmeasure.canadasroofer.com
X-API-Key: <ENGINE_API_KEY>
Content-Type: application/json

{
  "lat": 32.3347,
  "lon": -97.4153,
  "address": "1404 Wedgewood Dr Cleburne TX 76033",
  "strategy": "auto",       // optional: auto | lidar-first | solar-first | cache-only
  "force_refresh": false    // optional: skip cache
}
```

We respond with:

```json
{
  "success": true,
  "primary_source": "lidar",     // lidar | solar | hybrid | cache
  "confidence": 0.92,            // 0.0-1.0
  "total_area_m2": 505.3,
  "total_area_sqft": 5440,
  "predominant_pitch_deg": 26.6,
  "predominant_pitch_x_in_12": 6.0,
  "facets": [
    {
      "id": 0,
      "area_m2": 84.2,
      "area_sqft": 907,
      "pitch_deg": 26.6,
      "pitch_x_in_12": 6.0,
      "azimuth_deg": 180,
      "polygon_lonlat": [[-97.4153, 32.3347], ...],
      "provenance": "lidar_ransac"
    },
    ...
  ],
  "edges": {
    "ridges_hips_ft": 156.0,
    "valleys_ft": 22.5,
    "rakes_ft": 88.0,
    "eaves_ft": 102.0
  },
  "accessories": {
    "vents": 4,
    "pipes": 2,
    "skylights": 0
  },
  "imagery": {
    "aerial_url": "https://maps.googleapis.com/...",
    "street_view_url": "https://maps.googleapis.com/..."
  },
  "notes": ["OSMnx hit at 80m radius", "LIDAR 412 points within polygon", ...],
  "elapsed_s": 6.4
}
```

The portal owns:
- Geocoding `address` → `(lat, lon)` (with Google Geocoding API)
- Stripe subscription gating + quota enforcement
- PDF rendering from the measurement JSON
- Per-tenant branding (logo, primary color, contact info)
- Customer-facing UI and admin dashboard

The engine owns:
- Building footprint resolution
- LIDAR fetch + segmentation
- Solar API integration + quality fallback
- Edge classification (ridge/hip/valley/rake/eave)
- Accessory formulas (vents, pipes, skylights)
- Imagery URL generation
- Usage logging (per external-API call)

## 6. Coding style

- Black formatting, line length 100.
- Type hints required on all public functions.
- Use `logging.getLogger("roofmeasure.<module>")` — **never `print()`**.
- In-place patches use unique marker comments like `# OSM_SANITY_V312_INLINE`
  so the deploy script can detect what's already applied. **Always add a
  marker comment when patching a function.**
- Idempotency: every install / patch script must be re-runnable safely.
- Each external provider gets its own module (`nrcan_hrdem_provider.py`,
  `ms_global_ml.py`, etc.) and is wired into the fallback chain in
  `footprint_v2.get_building_footprint` or `hybrid_pipeline.measure_hybrid` —
  not anywhere else.

## 7. Do NOT touch list

- **`roofmeasure/segmentation_v2.py`** — algorithm is tuned against ground
  truth. Mean error 4.3% / median 3.7%. Changing it can regress all 7
  working addresses. If you must edit, run the full harness before and
  after.
- **`tests/ground_truth.csv`** — adding rows via `scripts/add_eagleview_pdf.sh`
  is fine; never edit existing rows.
- **Production environment** — don't change nginx config, TLS certs, or
  systemd units without testing on a staging branch first.
- **Google API key in any committed file** — never inline it in code.
  The key has been rotated once already after a leak.

## 8. Quality bar before merging

- All 11 ground-truth addresses still run (succeed or fail — but no new
  uncaught exceptions).
- Mean area error across succeeded addresses must not regress more than
  2 percentage points from baseline (~4.3%).
- `mypy roofmeasure/` passes with no new errors.
- `pytest tests/unit/` passes (unit tests, fast).

## 9. Ground-truth-driven development

The truth source is `tests/ground_truth.csv` + the harness. If you're not
sure whether a change is good, run the harness. On the VPS, an auto-test
watcher (`roofmeasure-auto-test.service`) re-triggers the harness on
every `.py` edit; log is at `/tmp/auto_test_watch.log`.

Locally, run it manually:

```bash
python tests/ground_truth_harness.py tests/ground_truth.csv
```

## 10. When in doubt

- Read `docs/DECISIONS.md` — most architecture questions are already
  answered there.
- Check `docs/OPEN_QUESTIONS.md` — your question might already be logged
  there. If not, add it.
- The user prefers **one step at a time** over big multi-step patches.
  Give a small probe command, see the output, then decide the next move.
