# Cursor Takeover Audit — `roofmeasure-engine`

**Author:** Cursor (Claude Opus 4.7)
**Date:** 2026-05-27
**Repo:** `github.com/billilkov-beep/roofmeasure-engine` (branch `main`, working tree clean)
**Scope:** This audit covers the **engine** repo. Its sole known client today is the
companion repo `roofmeasure-portal`, which is audited in its own
`docs/CURSOR_TAKEOVER_AUDIT.md`.
**Status:** Read-only audit. No code, deploys, commits, or pushes have been performed.

> **About the prompt's expected handoff files.** The takeover prompt asked me to read
> `AGENTS.md`, `PROJECT_BRIEF.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `DEPLOYMENT_HOSTINGER.md`,
> `.env.example`, `docs/DECISIONS.md`, `docs/OPEN_QUESTIONS.md`, `.cursor/rules/project.mdc`.
>
> **None of those exist in this repo.** The closest substitutes are:
>
> - `README.md` — covers ~80% of what `PROJECT_BRIEF.md` + `ARCHITECTURE.md` + `ROADMAP.md` would
> - `deploy/DEPLOYMENT.md` — generic Ubuntu/nginx/systemd deploy, not Hostinger-specific
> - `deploy/roofmeasure-engine.env.example` — system env file template (not a dev `.env`)
> - `UPLOAD-TO-GITHUB.md` — one-time initial-push instructions (not a handoff doc)
>
> Everything else in this audit was reverse-engineered from the source.

---

## 1. What `roofmeasure-engine` does

This repo is the **Python measurement engine**. Production deployment runs it as a long-lived
HTTP service on an Ubuntu VPS at `https://lidar-worker.canadasroofer.com`. Given a street
address, the engine returns a JSON `RoofMeasurement` payload (per-facet area / pitch / azimuth,
edge classifications, obstructions, accessory take-off, confidence score).

Top-level entry points:

| Entry point | Purpose |
|---|---|
| `measure.py` (CLI) | `python3 measure.py "<address>"` — one-shot measurement to stdout/file |
| `measure.py --serve <port>` (HTTP) | `python3 measure.py --serve 8088` — long-running server |
| `roofmeasure.measurement.measure_roof(address, ...)` (library) | the same orchestrator, importable |

Pipeline (orchestrated by `roofmeasure/measurement.py:measure_roof`):

```
   address (string)
     │
     ▼  geocode.py:geocode_address
   GeocodeResult{lat, lon, source}        # Google → US Census → Nominatim
     │
     ▼  footprint.py:get_building_footprint
   BuildingFootprint{polygon_lonlat, source}   # OSM Overpass → Microsoft Building Footprints
     │
     ├─► STRATEGY = lidar_only / auto (LiDAR primary)
     │     ▼  lidar.py:fetch_lidar_for_footprint
     │   LidarCrop{points_local_m, source}    # USGS 3DEP → OpenTopography → NRCan (CA)
     │     ▼  segmentation.py:segment_roof
     │   RoofSegmentation{facets, edges}      # RANSAC plane fit + edge classification
     │     ▼  obstructions.py:detect_obstructions_from_residuals
     │   List[Obstruction]                    # bumps above plane (vents/chimneys/HVAC)
     │
     ├─► STRATEGY = solar_only / solar_first / auto fallback
     │     ▼  providers/google_solar.py:measure_via_solar_api
     │   Building Insights API response       # paid: ~$0.50/req
     │     ▼  data_layers.py:add_polygons_to_facets   (optional, uses dataLayers GeoTIFFs)
     │   facets[]                             # per-facet polygons in EPSG:4326
     │
     ▼  measurement.py:_build_measurement
   RoofMeasurement (dataclass)
     │
     ▼  accessories.py:estimate_accessories
   accessoryTakeoff[] + line_measurements{}  # shingles, ridge cap, gutters, etc.
     │
     ▼  imagery.py:fetch_property_imagery (optional)
   Street View + aerial URLs / cached files  # uses GOOGLE_MAPS_API_KEY
     │
     ▼  usage.py:time_call (always)
   SQLite row in /var/lib/roofmeasure/usage.db
     │
     ▼  to_json()
   { roofAreaSqFt, facets[], edges[], lineMeasurements{}, accessoryTakeoff[], … }
```

HTTP surface (`measure.py:serve_http`):

| Method | Path | Auth | Body / Query | Returns |
|---|---|---|---|---|
| GET  | `/health` | none | — | `{status,auth_required,admin_enabled,version,strategy}` |
| POST | `/measure` | `X-API-Key` | `{address, synthetic?, strategy?}` | full `RoofMeasurement` JSON |
| GET  | `/admin/strategy` | `X-Admin-Key` | — | `{strategy,envDefault,persisted,validStrategies}` |
| POST | `/admin/strategy` | `X-Admin-Key` | `{strategy, who?}` | new persisted config |
| GET  | `/admin/usage` | `X-Admin-Key` | `?from=YYYY-MM-DD&to=YYYY-MM-DD` | aggregated usage from SQLite |
| OPTIONS | `*` | none | — | CORS preflight (if `ROOFMEASURE_ALLOW_ORIGIN` set) |

The server is **Python's stdlib `ThreadingHTTPServer`**. No gunicorn, no uvicorn, no FastAPI.
This is intentional in the README ("prototype scale; gunicorn is a follow-up") but is one of
the production risks below.

Valid strategies (`roofmeasure/runtime_config.py:VALID_STRATEGIES`):
`auto` (LiDAR primary, Solar fallback) · `lidar_only` · `solar_only` · `solar_first` (Solar primary, LiDAR fallback).

## 2. What `roofmeasure-portal` does (relevant context for engine work)

The companion repo `roofmeasure-portal` is the **only known consumer** of this engine. It's a
Next.js 14 site hosted on Hostinger Cloud Hosting at `https://roofmeasure.canadasroofer.com`.

When working on the engine, the relevant facts about the portal are:

- It posts to `${LIDAR_WORKER_URL}/measure` with `X-API-Key`.
- It calls `${LIDAR_WORKER_URL}/admin/strategy` and `/admin/usage` with `X-Admin-Key`.
- It expects the response shape produced by `RoofMeasurement.to_json()` in
  `roofmeasure/measurement.py`. Field names are camelCase (e.g. `roofAreaSqFt`,
  `lineMeasurements`, `accessoryTakeoff`).
- It treats `engineUsed`, `costCents`, and `durationMs` as optional top-level fields
  the engine may attach (today these are not consistently emitted — see §12).
- It overrides `imagery.streetViewUrl` and aerial URLs with its own values; the engine's
  `imagery` block is computed but mostly unused.
- It does **not** use the `integration/measurement-client.ts` adapter or the
  `integration/estimate-engine.patch.md` recipe in this repo — both are obsolete (see §11–§12).

## 3. How the two repos connect

```
+-------------------------------+  HTTPS POST  +-------------------------------+
| Hostinger Cloud Hosting       │ /measure     | Hostinger VPS                 |
| https://roofmeasure.canada... │─────────────►│ https://lidar-worker.canada...│
|                               │ (X-API-Key)  |                               |
| roofmeasure-portal (Next 14)  │              | nginx:443                     |
|  app/api/estimate/create      │              |  ├─ checks /etc/nginx/        |
|  lib/lidar-worker.ts ─────────┼──/health─────┤  │  lidar_api_keys.map        |
|                               │              |  ├─ rate-limits 30 r/min/IP   |
|  Admin UI:                    │              |  └─ proxies 127.0.0.1:8088    |
|  app/admin/strategy ──────────┼──/admin/*────┤                               |
|  app/admin/usage              │ (X-Admin-Key)| systemd: roofmeasure-engine   |
+-------------------------------+              |  measure.py --serve 8088      |
                                               |  user: roofmeasure            |
                                               |  envfile: /etc/roofmeasure-   |
                                               |          engine.env (600)    |
                                               |                               |
                                               |  Persistent state:            |
                                               |   /var/lib/roofmeasure/       |
                                               |     runtime.json (strategy)   |
                                               |     usage.db (SQLite)         |
                                               |   /var/cache/roofmeasure/     |
                                               |     LAZ tile cache, imagery   |
                                               |                               |
                                               |  Outbound:                    |
                                               |   - Google Geocoding (opt)    |
                                               |   - OSM Overpass              |
                                               |   - MS Building Footprints    |
                                               |   - USGS 3DEP / OpenTopography│
                                               |   - Google Solar API (paid)   |
                                               |   - Google Street View/Static |
                                               +-------------------------------+
```

Important consequence for engine work: **the engine never calls back into the portal.**
Anything the portal needs from the engine has to be in the `/measure` or `/admin/*` response.

## 4. Which repo deploys to Hostinger

Both do, but **on different Hostinger products**:

| Repo | Hostinger product | URL | Deploys defined in |
|---|---|---|---|
| `roofmeasure-engine` (this repo) | **Hostinger VPS** (Ubuntu 22.04 + nginx + systemd + certbot) | `https://lidar-worker.canadasroofer.com` | `deploy/` folder + `.github/workflows/deploy-engine.yml` |
| `roofmeasure-portal` | Hostinger Cloud Hosting, Node.js application | `https://roofmeasure.canadasroofer.com` | `HOSTINGER_DEPLOY.md` (manual / hPanel Git auto-pull) |

The engine's deploy directory (`deploy/`) is **the actual source of truth** for what runs on
the VPS:

- `deploy/nginx.conf` → `/etc/nginx/sites-available/lidar-worker.conf`
- `deploy/lidar_api_keys.map.example` → `/etc/nginx/lidar_api_keys.map`
- `deploy/roofmeasure-engine.service` → `/etc/systemd/system/roofmeasure-engine.service`
- `deploy/roofmeasure-engine.env.example` → `/etc/roofmeasure-engine.env`

## 5. Do both repos deploy, or only one?

**Both deploy, independently.** From this repo's perspective:

- This repo has a working GitHub Actions deploy pipeline:
  `.github/workflows/deploy-engine.yml`. Triggered by `workflow_dispatch` or pushes to `main`
  touching `roofmeasure/`, `measure.py`, `requirements.txt`, `deploy/`, or the workflow itself.
- The job runs `tests/test_segmentation.py`, `tests/test_google_solar.py`,
  `examples/demo_offline.py` as offline smoke tests, then rsyncs to the VPS over SSH, restarts
  the systemd unit, and curls `/health` + `/measure?synthetic=true` for a post-deploy check.
- The portal has **no GitHub Actions deploy** — it's updated manually or via Hostinger's
  built-in Git auto-pull. Pushing to this repo will not touch the portal.

Required GitHub Actions secrets (from the workflow header comment):

| Secret | Purpose |
|---|---|
| `DEPLOY_HOST` | e.g. `lidar-worker.canadasroofer.com` |
| `DEPLOY_USER` | SSH user with passwordless sudo for `systemctl restart/status roofmeasure-engine` |
| `DEPLOY_SSH_KEY` | private key contents (ed25519 preferred) |
| `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan -H <host>` output |
| `ENGINE_API_KEY` | same value as `ROOFMEASURE_API_KEY` on the VPS, used for the smoke test |
| `SLACK_WEBHOOK_URL` (optional) | notify on failure |

## 6. Current tech stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.11 (per workflow `actions/setup-python@v5 with: python-version: '3.11'`) |
| HTTP server | `http.server.ThreadingHTTPServer` (stdlib) in `measure.py:serve_http` |
| Core deps (`requirements.txt`) | `numpy>=1.24`, `requests>=2.30`, `laspy>=2.4`, `pyproj>=3.6`, `shapely>=2.0`, `pillow>=10.0` |
| **Solar polygon mode extras (NOT in `requirements.txt`)** | `tifffile`, `scipy`, `scikit-image` (imported by `roofmeasure/data_layers.py`) |
| **v2 modules extras (NOT in `requirements.txt`)** | `py3dep` (`lidar_v2.py`), `osmnx` + `geopandas` (`footprint_v2.py`), `open3d` (`segmentation_v2.py`), `tifffile` |
| Geometry | Pure numpy in v1 (`segmentation.py`); Open3D in v2 (`segmentation_v2.py`, not wired in) |
| Persistent storage | SQLite (`/var/lib/roofmeasure/usage.db`, see `usage.py`), JSON (`/var/lib/roofmeasure/runtime.json`, see `runtime_config.py`), file cache (`/var/cache/roofmeasure/`, see `lidar.py` and `imagery.py`) |
| Edge | nginx (`deploy/nginx.conf`): TLS via certbot/Let's Encrypt, per-IP rate limit `30 r/min`, API key allowlist in `/etc/nginx/lidar_api_keys.map`, 120 s upstream timeout |
| Process supervision | systemd unit (`deploy/roofmeasure-engine.service`) with hardening: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ReadWritePaths=/var/cache/roofmeasure /opt/roofmeasure/data` |
| Auth model | API key (`X-API-Key` → `/measure`) and admin key (`X-Admin-Key` → `/admin/*`), enforced at **both** nginx (defense-in-depth allowlist) and the Python handler (`hmac.compare_digest`) |
| Logging | journald via systemd; structured logger in each module under `LOG = logging.getLogger(__name__)` |
| External APIs | Google Geocoding (optional), OSM Overpass, Microsoft Building Footprints, USGS 3DEP, OpenTopography, Google Solar API (paid), Google Street View Static, Google Maps Static |
| CI/CD | `.github/workflows/deploy-engine.yml` (only) |
| Tests | `tests/test_segmentation.py`, `tests/test_google_solar.py`, `examples/demo_offline.py`, `roofmeasure/test_v2_pipeline.py` (deploy-side smoke) |
| Versioning | `server_version = "RoofMeasureEngine/0.3"` (in `measure.py`) and `"RoofMeasureEngine/0.1"` (in `geocode.py` UA) disagree. README mentions v0.2/v0.4/v0.5/v0.6. No git tags. |

## 7. Required environment variables

Loaded by systemd from `/etc/roofmeasure-engine.env` (template:
`deploy/roofmeasure-engine.env.example`). All vars also accept being set directly in the shell
when running the CLI.

### Engine / service

| Var | Required? | Default | Where it's used |
|---|---|---|---|
| `ROOFMEASURE_API_KEY` | **yes** (in prod) | — | `measure.py:_key_ok` for `/measure`; mirrored into `/etc/nginx/lidar_api_keys.map`. If unset, server starts in open mode and logs a warning. |
| `ROOFMEASURE_ADMIN_KEY` | **yes** (for admin) | — | `measure.py:_key_ok` for `/admin/*`. If unset, admin routes are open. |
| `ROOFMEASURE_STRATEGY` | no | `auto` | `runtime_config.py:get_strategy` (fallback when runtime.json absent). Valid: `auto`, `lidar_only`, `solar_only`, `solar_first`. |
| `ROOFMEASURE_CONFIG_FILE` | no | `/var/lib/roofmeasure/runtime.json` | persisted strategy config |
| `ROOFMEASURE_USAGE_DB` | no | `/var/lib/roofmeasure/usage.db` | SQLite usage log |
| `ROOFMEASURE_CACHE_DIR` | no | `/var/cache/roofmeasure` | LAZ tile cache, imagery cache |
| `ROOFMEASURE_ALLOW_ORIGIN` | no | unset | If set, enables CORS for that origin |
| `ROOFMEASURE_USER_AGENT` | no | `RoofMeasureEngine/0.1 (contact: support@canadasroofer.com)` | UA on outbound HTTP (Overpass, USGS, etc.) |

### External provider keys

| Var | Required for | Notes |
|---|---|---|
| `GOOGLE_SOLAR_API_KEY` | Any Solar API path | Google Cloud key with "Solar API" enabled. Billed ~$0.50/call. |
| `GOOGLE_SOLAR_REQUIRED_QUALITY` | Solar path | `HIGH` (best accuracy) / `MEDIUM` (production default per README) / `LOW`. `roofmeasure-engine.env.example` ships `HIGH`. |
| `GOOGLE_GEOCODING_API_KEY` | optional | If unset, geocode falls back to US Census → Nominatim. |
| `GOOGLE_MAPS_API_KEY` | optional | Used by `imagery.py` for Street View Static + Maps Static. If unset, `imagery` block in response is empty. |
| `OPENTOPO_API_KEY` | optional | Easiest LiDAR path (free, sign-up at opentopography.org). |
| `MSBF_LOCAL_MIRROR` | optional | Path to a local mirror of Microsoft Building Footprints. |

### Cost overrides

| Var | Default | Used by |
|---|---|---|
| `SOLAR_API_COST_CENTS` | `50` | `usage.py` cost-per-call |
| `LIDAR_ENGINE_COST_CENTS` | `0` | `usage.py` cost-per-call |

### What the **portal** sends — i.e. what we must accept

The portal expects to set, and the engine must honor, the following headers and body fields:

- Header `X-API-Key: <value of ROOFMEASURE_API_KEY>` on `POST /measure`
- Header `X-Admin-Key: <value of ROOFMEASURE_ADMIN_KEY>` on `/admin/*`
- Body `{"address": "...", "structure": "house"|"garage"|"house_garage", "lat": <float>, "lng": <float>, "strategyOverride": <string>}`

Note: today the engine **ignores** `structure`, `lat`, `lng`, and `strategyOverride` from the
body — only `address`, `synthetic`, and `strategy` are read in `do_POST` for `/measure`. See §12.

## 8. Build commands

There is no compile step. The "build" is just dependency installation.

```bash
# minimum (CLI + LiDAR + offline tests)
pip3 install -r requirements.txt

# add Solar dataLayers polygon mode (data_layers.py imports)
pip3 install tifffile scipy scikit-image

# add v2 modules (footprint_v2.py / lidar_v2.py / segmentation_v2.py)
pip3 install py3dep osmnx geopandas open3d
```

On the production VPS, deps are installed once per user:

```bash
sudo -u roofmeasure pip3 install --user numpy requests laspy
```

(per `deploy/DEPLOYMENT.md` step 5). **Note:** that command does **not** install
`pyproj`, `shapely`, `pillow`, `tifffile`, `scipy`, `scikit-image`, `py3dep`, `osmnx`, or
`open3d`. Any code path requiring those will fail at import on a fresh install — see §12.

## 9. Test commands

```bash
# Offline smoke tests (no network, < 5 s each)
python3 tests/test_segmentation.py        # synthetic hip roof → 4 facets, area within 0.2%
python3 tests/test_google_solar.py        # parse fixture Solar response → measurement dict
python3 examples/demo_offline.py          # full pipeline against synthetic LiDAR

# v2 pipeline deploy-side smoke (hard-codes /home/roofmeasure/engine — see §12 risk #14)
python3 roofmeasure/test_v2_pipeline.py

# Live integration (needs network + API keys)
python3 measure.py "624 Merrill Ave, Bedford, OH"          # US, LiDAR path
python3 measure.py --strategy solar_only "<address>"        # paid Solar API path
python3 measure.py --synthetic "<any address>"              # skip LiDAR fetch, synthesize cloud
python3 measure.py --strategy solar_first --verbose "..."   # full debug logs

# Live HTTP smoke after deploy (uses synthetic to avoid USGS dependency)
curl -fsS https://lidar-worker.canadasroofer.com/health
curl -fsS -X POST https://lidar-worker.canadasroofer.com/measure \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: <key>" \
  -d '{"address":"624 Merrill Ave, Bedford, OH","synthetic":true}'
```

There are **no unit tests for `accessories.py`, `obstructions.py`, `data_layers.py`,
`runtime_config.py`, `usage.py`, or `imagery.py`**. There is no pytest config, no
`tests/__init__.py`, and `tests/` is not on the path by default (each test file does its own
`sys.path.insert(0, ...)`).

## 10. Run commands

```bash
# Local CLI
python3 measure.py "<address>"                       # one-shot JSON to stdout
python3 measure.py "<address>" --out result.json     # write to file
python3 measure.py --serve 8088                      # HTTP on 0.0.0.0:8088

# CLI flags
python3 measure.py --strategy auto "<address>"       # auto | lidar_only | solar_only | solar_first
python3 measure.py --synthetic "<address>"           # skip LiDAR fetch, synthesize cloud from footprint
python3 measure.py --verbose "<address>"             # DEBUG-level logging

# Production (systemd-managed, on the VPS)
sudo systemctl start    roofmeasure-engine
sudo systemctl restart  roofmeasure-engine
sudo systemctl status   roofmeasure-engine
sudo journalctl -u      roofmeasure-engine -f
```

## 11. Missing documentation

Compared to the standard handoff checklist used by the takeover prompt:

| File | Status in this repo | Closest existing alternative |
|---|---|---|
| `AGENTS.md` | **missing** | — |
| `PROJECT_BRIEF.md` | **missing** | `README.md` (long-form, mixes brief + arch + roadmap) |
| `ARCHITECTURE.md` | **missing** | `README.md` "Architecture" ASCII diagram (engine-only, no portal context) |
| `ROADMAP.md` | **missing** | `README.md` sections "What's NOT in the prototype (roadmap)", "Roadmap (post v0.4)", "v0.5/v0.6 additions" |
| `DEPLOYMENT_HOSTINGER.md` | **missing** | `deploy/DEPLOYMENT.md` (generic Ubuntu, mentions Hostinger KVM2 only as one option) |
| `.env.example` (at root) | **missing** | `deploy/roofmeasure-engine.env.example` (system-level env, not a dev-time `.env`) |
| `docs/DECISIONS.md` | **missing** | scattered "why" notes in module docstrings |
| `docs/OPEN_QUESTIONS.md` | **missing** | — |
| `.cursor/rules/project.mdc` | **missing** | no `.cursor/` directory |

Other gaps a new contributor will hit:

- **No API spec.** The `/measure` response shape is defined only by the
  `RoofMeasurement` dataclass in `roofmeasure/measurement.py` and the way `to_json()` serializes
  it. There is no OpenAPI doc, no JSON Schema, no `examples/sample_response.json` (the closest is
  `examples/demo_offline_output.json`).
- **No CHANGELOG.** README casually references v0.1 / v0.2 / v0.4 / v0.5 / v0.6 / v0.8, but
  there are no git tags, no `CHANGELOG.md`, and the `server_version` constant in `measure.py`
  says `RoofMeasureEngine/0.3`. So there are at least three competing version numbers.
- **No `CONTRIBUTING.md`** and no `pyproject.toml` / `setup.py` (this is not a packaged library).
- **`UPLOAD-TO-GITHUB.md`** is a one-time bootstrap doc for the very first push to GitHub. It
  is no longer relevant for ongoing work and could mislead new contributors.

## 12. Anything broken or unclear

### Broken / inconsistent (engine-side)

1. **`data_layers.py` will `ImportError` on a default install.** It imports `tifffile`,
   `scipy.ndimage`, `scipy.ndimage.cc_label`, `skimage.measure`, and `pyproj.Transformer`.
   Of those, only `pyproj` is in `requirements.txt`; the rest are not. As a result, **the
   Solar polygon extraction path crashes the moment `providers/google_solar.py` imports it
   (line 40: `from roofmeasure.data_layers import add_polygons_to_facets`)**. That import
   happens at the top of the module, not lazily, so any Solar call will fail before reaching
   `measure_via_solar_api`.
2. **v2 modules (`footprint_v2.py`, `lidar_v2.py`, `segmentation_v2.py`) are not wired in.**
   `measurement.py` still imports v1 versions:
   `from .footprint import …`, `from .lidar import …`, `from .segmentation import …`.
   Latest commit (`9f26ea1 Phase 1: add v2 library-based modules`) says this is mid-migration,
   but there is no flag, env var, or strategy that switches v1→v2.
3. **Deploy workflow's test job under-installs.** `.github/workflows/deploy-engine.yml`
   installs only `numpy requests` in the smoke-test job, then runs `tests/test_segmentation.py`
   (numpy-only — fine), `tests/test_google_solar.py` (imports
   `providers.google_solar` → imports `data_layers` → needs `tifffile, scipy, skimage`), and
   `examples/demo_offline.py` (imports `obstructions`/`segmentation`/`measurement`).
   **`tests/test_google_solar.py` will fail in CI today** because `tifffile` is missing.
   (Or it's passing only because the import is deferred — verify before relying on the
   smoke gate.)
4. **`roofmeasure/test_v2_pipeline.py` hard-codes `/home/roofmeasure/engine` in `sys.path`.**
   The production install path documented in `deploy/DEPLOYMENT.md` is `/opt/roofmeasure`.
   Running the v2 smoke test on the actual deploy target will fail with `ModuleNotFoundError`.
5. **`integration/` folder is mostly obsolete.** It describes a portal integration that the
   current portal no longer uses:
   - `integration/measurement-client.ts` and `integration/estimate-engine.patch.md` reference
     `ENABLE_LIDAR_WORKER`, `lib/estimate-engine.ts`, and a hash-heuristic fallback that don't
     exist in the current `roofmeasure-portal` codebase.
   - `integration/admin-page/` contains proposed Next.js page snippets the portal has long
     since replaced with its own implementations.
   - `integration/report-pdf-patch/` is a partial diff for a portal PDF that has been
     completely rewritten.
   - **`integration/e2e-tests/` belongs in the portal repo, not the engine repo.** The specs
     exercise customer + admin flows on the Next.js site; nothing here is engine-testing.
6. **Solar request body sends `requiredQuality` as a query parameter,** but the Google Solar
   API actually accepts it as a query param on `findClosest` — confirm this matches the latest
   Solar API contract; the API has changed at least once.
7. **`measure.py` does not pass `lat`, `lng`, `structure`, or `strategyOverride` from the
   request body through to the engine.** The portal sends them; `do_POST` reads only
   `address`, `synthetic`, `strategy`. Net effect: the portal's per-request lat/lng hint and
   structure selector are silently dropped.
8. **`engineUsed` / `costCents` / `durationMs` are not always emitted on the top-level
   response.** They're written into `usage.db` via `time_call.record(engine=…)`, but the
   `RoofMeasurement` dataclass in `measurement.py` doesn't have those fields. The portal
   reads `raw.engineUsed` and `raw.costCents` from the HTTP response and falls back to
   defaults when missing. So the portal's `/admin/usage` table shows "—" for engine name on
   most rows.
9. **Strategy persistence directory must pre-exist.** `runtime_config.py:set_runtime_config`
   does `os.makedirs(os.path.dirname(path), exist_ok=True)` — that works **if** the service
   user has write permission to `/var/lib`. With `ProtectSystem=strict` in the systemd unit
   plus `ReadWritePaths=/var/cache/roofmeasure /opt/roofmeasure/data` (note: not
   `/var/lib/roofmeasure`), **the engine cannot write `runtime.json` at all unless that path
   is added to `ReadWritePaths` or the directory is pre-created and granted explicitly.**
   `deploy/DEPLOYMENT.md` step 4 only creates `/opt/roofmeasure/data` and `/var/cache/roofmeasure`.
10. **`usage.py:_write_row` silently swallows `sqlite3.OperationalError`.** If the DB path is
    read-only (likely under `ProtectSystem=strict` — see #9), every call logs but the row
    never lands. `/admin/usage` will return zeros while real Solar API spend is happening.
11. **`server_version` lies.** `measure.py` reports `RoofMeasureEngine/0.3` on `/health` and
    in nginx `Server:` headers, but the codebase has shipped through v0.4/v0.5/v0.6/v0.8
    feature drops since.
12. **No graceful shutdown.** `ThreadingHTTPServer` has no `try: serve_forever() finally:
    server.shutdown()`. A `systemctl restart roofmeasure-engine` drops in-flight LAZ
    downloads. (Acceptable for prototype scale; documented in `deploy/DEPLOYMENT.md` §14.)
13. **`__init__.py` is empty.** That's fine, but the README's "Repository layout" implies it
    re-exports something. It doesn't — every consumer has to import from submodules.
14. **CORS is all-or-nothing.** `ROOFMEASURE_ALLOW_ORIGIN` accepts a single string and is
    echoed verbatim. No wildcard handling, no multi-origin support. Fine for the
    single-portal deployment, but worth knowing before adding a staging origin.

### Unclear / undocumented

15. The `imagery.fetch_property_imagery` function returns `aerial_url`, `aerial_path`,
    `aerial_zoom`, `street_view_*`, and a `note`. The portal **does not use any of these**
    (it generates its own Street View + Maps Static URLs). Either delete the engine-side
    imagery fetch (saves Google Static API calls) or update the portal to consume them.
16. Confidence score (`_build_measurement` in `measurement.py`) starts at 70 and can go
    negative before the `max(35, min(98, ...))` clamp if `seg.notes` is long. The clamp
    hides bad scoring rather than reflecting it. There's no test coverage on the score.
17. The `structure` enum from the portal is intended to let users measure "garage only" or
    "house + garage" separately. The engine does not currently distinguish — it measures the
    single building containing the geocoded point. Worth deciding whether this is a stated
    limitation or a TODO.
18. The Solar API path (`google_solar.py:measure_via_solar_api`) returns a dict; the LiDAR
    path returns a `RoofMeasurement` dataclass; `_measure_via_solar` wraps the dict in
    `RoofMeasurement(**data)`. There is no schema check that the dict keys match the
    dataclass fields. Adding a field to one path without the other would break silently.
19. The "v0.5 Playwright e2e suite" mentioned in README lives at `integration/e2e-tests/` but
    is engineered against the portal. It cannot run against the engine alone. The README and
    `integration/e2e-tests/README.md` need to clearly state "run this against the deployed
    portal, after pointing it at this engine."
20. No backup script for `/var/lib/roofmeasure/usage.db` or `/var/lib/roofmeasure/runtime.json`.

## 13. Top 10 next development tasks

In rough priority order. Each is one focused PR's worth of work.

1. **Fix `data_layers.py` dependency mismatch.** Add `tifffile`, `scipy`, `scikit-image` to
   `requirements.txt`, OR move the imports inside `add_polygons_to_facets()` so the module
   imports cleanly without them, OR feature-gate the polygon extraction behind a
   `try/except ImportError`.
2. **Fix the deploy workflow's test environment** to install everything the smoke tests
   actually need (`numpy requests tifffile scipy scikit-image` at minimum). Without this,
   CI is green only because we got lucky with import order.
3. **Add `/var/lib/roofmeasure` to `ReadWritePaths` in the systemd unit,** and update
   `deploy/DEPLOYMENT.md` step 4 to create the directory + chown it. Today
   `runtime.json` strategy changes likely fail silently in production.
4. **Resolve the v2 cutover.** Either wire v2 into `measurement.py` behind a
   `ROOFMEASURE_ENGINE_VERSION=v2` env flag with a regression test, or move v2 files to
   `prototypes/` so they don't appear production-ready.
5. **Delete the obsolete `integration/` folder.** Specifically:
   `integration/estimate-engine.patch.md`, `integration/measurement-client.ts`, the entire
   `integration/admin-page/` folder, and `integration/report-pdf-patch/`. Move
   `integration/e2e-tests/` into the portal repo (it was always portal code).
6. **Honor request-body hints.** Read `lat`, `lng`, `structure`, `strategyOverride` in
   `do_POST` `/measure`. Skip the geocode call if the portal already supplied a valid
   lat/lng. This will measurably reduce Google Geocoding spend.
7. **Emit `engineUsed`, `costCents`, `durationMs` on the response.** Add the three fields to
   the `RoofMeasurement` dataclass, set them in `_measure_via_lidar` and `_measure_via_solar`,
   and confirm `/admin/usage` on the portal starts showing per-engine counts.
8. **Reconcile `server_version`.** Pick one version string, tag a git release matching it,
   and bump the constant + `Server:` header on every release.
9. **Replace stdlib `ThreadingHTTPServer` with gunicorn + a tiny WSGI shim.**
   `deploy/DEPLOYMENT.md` already calls this out as a follow-up. Provides graceful shutdown,
   worker isolation, and stdlib-free deployment for multi-instance.
10. **Add the missing handoff docs.** `AGENTS.md`, `PROJECT_BRIEF.md`, `ARCHITECTURE.md`,
    `ROADMAP.md`, `docs/DECISIONS.md`, `docs/OPEN_QUESTIONS.md`. They can be short. Add
    them as part of normal PRs from now on.

## 14. Risks before production deployment

Engine-side risks, severity-ordered. None are theoretical.

1. **Solar API quota exhaustion ($0.50/call, no built-in cap).** With the portal's
   `/api/estimate/create` open to the public and no portal-side rate limit, a script kiddie
   can submit addresses non-stop. The engine has nginx rate limits (30 r/min/IP) but a
   distributed scrape would still drain the Google billing limit. There is **no
   monthly-cap / circuit-breaker** in `measurement.py` despite the README listing it as a
   roadmap item. Mitigation: set a hard Google Cloud Billing budget alert AND implement an
   in-engine "stop calling Solar if cost_cents-this-month > $X, flip to lidar_only" check
   in `time_call` or `_measure_via_solar`.
2. **`data_layers.py` import failure (see §12 #1).** The first real Solar request after a
   clean deploy will throw `ImportError: tifffile`, the engine will return HTTP 500, and the
   portal will surface "Engine unavailable" to customers. High-impact, easy to fix.
3. **`/var/lib/roofmeasure` write-protection issue (see §12 #9).** Admin strategy changes
   may not persist. Worse: the next deploy reboots the service to `ROOFMEASURE_STRATEGY=auto`
   from env, even if an admin had set it to `lidar_only` to control spend.
4. **API key in plaintext on the VPS.** `/etc/roofmeasure-engine.env` mode 600,
   `/etc/nginx/lidar_api_keys.map` mode 600 — fine. But anyone with SSH on the VPS reads
   both. Rotate quarterly (procedure documented in `deploy/DEPLOYMENT.md` §12). Make sure
   GitHub Secrets are rotated in lockstep.
5. **No graceful shutdown** (see §12 #12). A `systemctl restart` during a 60 s LAZ download
   drops the connection; the portal surfaces an opaque error. The deploy workflow restarts
   the service on every push to `main`. Mitigation: add a deploy-time drain step, or move to
   gunicorn before the user count gets above a handful per minute.
6. **No alerting on engine-internal failures.** Errors are logged to journald only. The
   portal has an optional `ALERT_WEBHOOK_URL` for HTTP-layer errors, but pure engine errors
   (LAZ download timeout, Solar API 503, segmentation throwing) only surface in the portal's
   error log when the engine returns 500. No PagerDuty / Slack / OpsGenie hookup on the
   engine itself.
7. **No regression test on real LiDAR data.** All tests are synthetic. The first time a real
   USGS tile is read after a refactor, the team finds out in production whether
   `segmentation.py` still handles real-world point density variations correctly. Mitigation:
   commit one anonymized small LAZ tile and add a real-data smoke test.
8. **Unbounded LAZ cache growth.** `/var/cache/roofmeasure/` accumulates LAZ tiles forever;
   `lidar.py` writes but never evicts. A typical urban tile is 50–200 MB. Mitigation: add a
   tmpwatch / `find -mtime +60 -delete` cron, or implement an LRU eviction.
9. **`/health` doesn't actually probe outbound connectivity.** It only reports
   `auth_required` / `admin_enabled` / `strategy` flags. If USGS is down, Solar API is down,
   or Overpass is down, `/health` returns 200 and uptime monitors stay green while every
   `/measure` returns 500.
10. **Solar API key has Geocoding + Maps Static + Street View Static enabled on it.** The
    portal also uses these. If the key gets compromised on the engine, the same key drains
    quota for the portal's Street View calls. Mitigation: separate keys per API per
    component, restricted by HTTP referrer / IP.
11. **The `roofmeasure` user has no shell** (`/usr/sbin/nologin`, per `deploy/DEPLOYMENT.md`)
    but **does** have write access to `/opt/roofmeasure/data` (per the systemd
    `ReadWritePaths`). The engine doesn't write to `/opt/roofmeasure/data` today. If it ever
    starts (e.g. caching), the deploy `rsync --delete` will wipe whatever it cached.
12. **Address strings are PII.** `usage.py` hashes them to sha1 before storing, which is good,
    but addresses also appear in journald logs (`LOG.info("geocoded %r -> ...", address)`,
    `LOG.info("calling Google Solar API for ..." )`). On a multi-tenant VPS, journald is
    readable by `adm`/`systemd-journal` group members. Mitigation: redact or hash before
    logging, or restrict journald ACLs.
13. **`integration/e2e-tests/` running against production creates real estimates** and
    consumes real Solar API quota every CI run if scheduled nightly. The portal's
    `/api/estimate/create` is the entry point; there's no "this is a test, skip Solar" flag.
14. **Documentation drift on the deploy paths** (`/opt/roofmeasure` vs.
    `/home/roofmeasure/engine` — see §12 #4). A new contributor following one doc and then
    the other gets stuck.
15. **No SBOM / dependency pinning.** `requirements.txt` uses `>=` for every dep.
    Reproducible deploy is impossible. A `numpy 2.0` release tomorrow could break
    segmentation today. Mitigation: pin to exact versions and use Dependabot.

---

## Appendix A — Engine repository layout

```
roofmeasure-engine/
  measure.py                            # CLI + HTTP server entry
  requirements.txt                      # core deps only (see §6 for what's missing)
  README.md                             # mixed brief + architecture + roadmap
  UPLOAD-TO-GITHUB.md                   # one-time bootstrap doc (obsolete)
  .gitignore
  .github/
    workflows/
      deploy-engine.yml                 # smoke-test → rsync → restart → curl /health
  roofmeasure/                          # the engine library
    __init__.py                         # empty
    geocode.py                          # Google → Census → Nominatim
    footprint.py                        # OSM Overpass → MS Building Footprints (v1)
    footprint_v2.py                     # OSMnx + MSBF (v2, NOT wired in)
    lidar.py                            # USGS 3DEP, OpenTopography, NRCan (v1)
    lidar_v2.py                         # py3dep (v2, NOT wired in)
    segmentation.py                     # numpy RANSAC + edge classifier (v1)
    segmentation_v2.py                  # Open3D (v2, NOT wired in)
    obstructions.py                     # LiDAR residual clustering, SAM2 stub
    accessories.py                      # rakes, flashing, gutters, downspouts, take-off
    measurement.py                      # orchestrator + RoofMeasurement dataclass
    imagery.py                          # Google Street View + Maps Static (mostly unused by portal)
    data_layers.py                      # Solar API GeoTIFF dataLayers + polygon extraction
                                        #   ↑ imports tifffile/scipy/skimage — NOT in requirements.txt
    runtime_config.py                   # strategy persistence to /var/lib/roofmeasure/runtime.json
    usage.py                            # SQLite usage log
    test_v2_pipeline.py                 # v2 smoke (hard-codes /home/roofmeasure/engine)
    providers/
      __init__.py                       # empty
      google_solar.py                   # Solar API adapter
  tests/
    test_segmentation.py                # synthetic hip roof
    test_google_solar.py                # parses fixture Solar response
  examples/
    demo_offline.py                     # full pipeline against synthetic data
    demo_offline_output.json
  deploy/                               # the source of truth for the VPS install
    DEPLOYMENT.md                       # step-by-step Ubuntu deploy
    nginx.conf                          # → /etc/nginx/sites-available/lidar-worker.conf
    lidar_api_keys.map.example          # → /etc/nginx/lidar_api_keys.map
    roofmeasure-engine.service          # → /etc/systemd/system/
    roofmeasure-engine.env.example      # → /etc/roofmeasure-engine.env
  integration/                          # mostly OBSOLETE (see §12 #5)
    measurement-client.ts               # old portal client, replaced by lib/lidar-worker.ts
    estimate-engine.patch.md            # patch for an old portal that no longer exists
    admin-page/                         # obsolete Next.js page snippets
    report-pdf-patch/                   # obsolete PDF patch
    e2e-tests/                          # PORTAL tests living in the wrong repo
```

## Appendix B — Where things live on the running VPS

| Concept | Location | Notes |
|---|---|---|
| Code | `/opt/roofmeasure` | Synced by `rsync` from `deploy-engine.yml` |
| Service user | `roofmeasure` | `useradd -r -s /usr/sbin/nologin` |
| Env file | `/etc/roofmeasure-engine.env` | chmod 600, chown root:roofmeasure |
| systemd unit | `/etc/systemd/system/roofmeasure-engine.service` | journald output |
| nginx site | `/etc/nginx/sites-enabled/lidar-worker.conf` → `sites-available/lidar-worker.conf` | |
| API key allowlist | `/etc/nginx/lidar_api_keys.map` | chmod 600 root:root |
| TLS certs | `/etc/letsencrypt/live/lidar-worker.canadasroofer.com/` | certbot auto-renew |
| Runtime config (strategy) | `/var/lib/roofmeasure/runtime.json` | needs writable dir — see §12 #9 |
| Usage log | `/var/lib/roofmeasure/usage.db` | SQLite, WAL mode |
| LAZ tile cache | `/var/cache/roofmeasure/` | unbounded, see §14 #8 |
| Imagery cache | `/var/cache/roofmeasure/imagery/` (if `imagery.py` ever fires) | unbounded |
| Logs | `journalctl -u roofmeasure-engine` + `/var/log/nginx/lidar-worker.{access,error}.log` | |
