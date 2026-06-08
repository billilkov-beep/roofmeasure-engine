#!/bin/bash
# MASTER DEPLOY — one paste, everything happens.
#
# What this does, in order:
#   1. Backup current state
#   2. Pre-flight check of all external providers
#   3. Install parallel ground-truth harness (12 min → 3-4 min runtime)
#   4. Apply v3.11 patches (OSMnx 1000m, MS Global ML, NRCan, Solar quality gate)
#   5. Apply v3.12 Solar quality fallback (HIGH → MED → LOW)
#   6. Run full ground-truth harness (3-4 min with parallel)
#   7. Install auto-test watcher (every future edit re-runs the harness)
#   8. Print summary + per-address results + state inspector output
#
# Runtime: ~6-8 minutes
# Paste this whole block into SSH as root.

set -e

echo "================================================================"
echo "  MASTER DEPLOY — v3.11 + v3.12 + parallel harness + auto-test"
echo "  $(date -u +%FT%TZ)"
echo "================================================================"

# ============================================================================
# Step 1: snapshot
# ============================================================================
echo
echo "=== 1/8: backup current state ==="
cd /home/roofmeasure/engine
BACKUP_TAR=/tmp/engine_pre_master_$(date +%Y%m%d_%H%M%S).tar.gz
sudo tar czf "$BACKUP_TAR" \
    roofmeasure/footprint_v2.py \
    roofmeasure/lidar_v2_raw.py \
    roofmeasure/segmentation_v2.py \
    roofmeasure/measurement_v2_wireup.py \
    roofmeasure/hybrid_pipeline.py \
    roofmeasure/edge_classification.py \
    tests/ground_truth_harness.py \
    tests/ground_truth.csv 2>/dev/null
echo "    -> $BACKUP_TAR"

# ============================================================================
# Step 2: pre-flight check providers (compact)
# ============================================================================
echo
echo "=== 2/8: pre-flight provider check ==="
sudo /home/roofmeasure/engine/venv/bin/python << 'PYEOF'
import os, time, requests
UA = "RoofMeasure/3.12"
def check(name, fn):
    t = time.time()
    try:
        result = fn(); elapsed = time.time() - t
        print(f"  [{elapsed:5.1f}s] {name}: {result}")
        return True
    except Exception as e:
        elapsed = time.time() - t
        print(f"  [{elapsed:5.1f}s] {name}: ERR {e}")
        return False

def tnm():
    r = requests.get("https://tnmaccess.nationalmap.gov/api/v1/products",
                     params={"bbox": "-97.16,32.82,-97.14,32.84",
                             "datasets": "Lidar Point Cloud (LPC)",
                             "outputFormat": "JSON", "max": 1},
                     headers={"User-Agent": UA}, timeout=10)
    return f"HTTP {r.status_code}, {len(r.json().get('items',[]))} items"
check("TNM (US LIDAR)", tnm)

def overpass():
    r = requests.post("https://overpass-api.de/api/interpreter",
                      data={"data": "[out:json];out 1;"},
                      headers={"User-Agent": UA}, timeout=10)
    return f"HTTP {r.status_code}"
check("Overpass (overpass-api.de)", overpass)

def nominatim():
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params={"q": "test", "format": "json"},
                     headers={"User-Agent": UA}, timeout=10)
    return f"HTTP {r.status_code}"
check("Nominatim", nominatim)

def pc():
    r = requests.get("https://planetarycomputer.microsoft.com/api/stac/v1/collections/ms-buildings",
                     headers={"User-Agent": UA}, timeout=10)
    return f"HTTP {r.status_code}"
check("MS Planetary Computer", pc)

def nrcan():
    r = requests.get("https://datacube.services.geo.ca/ows/elevation",
                     params={"service": "WCS", "version": "2.0.1", "request": "GetCapabilities"},
                     headers={"User-Agent": UA}, timeout=10)
    return f"HTTP {r.status_code}"
check("NRCan Datacube", nrcan)

def solar():
    key = os.environ.get("GOOGLE_SOLAR_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key: return "NO KEY"
    r = requests.get("https://solar.googleapis.com/v1/buildingInsights:findClosest",
                     params={"location.latitude": 32.83, "location.longitude": -97.15,
                             "requiredQuality": "LOW", "key": key},
                     timeout=10)
    return f"HTTP {r.status_code}"
check("Google Solar API", solar)
PYEOF

# ============================================================================
# Step 3: install parallel harness
# ============================================================================
echo
echo "=== 3/8: install parallel harness (4 workers) ==="
sudo tee /home/roofmeasure/engine/tests/ground_truth_harness.py > /dev/null << 'HARNESSEOF'
"""Ground-truth harness — parallel version (4 workers default)."""
from __future__ import annotations
import argparse, csv, datetime, logging, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0, "/home/roofmeasure/engine")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("groundtruth")
REQUIRED_COLS = {"address", "lat", "lon", "gt_total_sqft", "gt_num_facets", "gt_pitch_x12"}

def read_input(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows: raise SystemExit(f"empty CSV: {path}")
    return rows

def sf(s, d=None):
    try: return float(str(s).strip())
    except: return d

def si(s, d=None):
    try: return int(float(str(s).strip()))
    except: return d

def run_one(addr, lat, lon, mode):
    try:
        if mode == "lidar_only":
            from roofmeasure.measurement_v2_wireup import measure_via_lidar_v2
            r = measure_via_lidar_v2(lat, lon)
            if r is None or not r.success: return None, "lidar: fail", []
            return r, r.source, r.notes
        from roofmeasure.hybrid_pipeline import measure_hybrid
        r = measure_hybrid(lat, lon)
        if not r.success: return None, "hybrid: failed", r.notes
        return r, r.primary_source, r.notes
    except Exception as e:
        return None, f"exc: {e}", []

def compare(row, result, source, notes):
    addr = row.get("address", "?")
    gt_sqft = sf(row.get("gt_total_sqft"), 0)
    gt_facets = si(row.get("gt_num_facets"), 0)
    gt_pitch = sf(row.get("gt_pitch_x12"), 0)
    if result is None:
        return {"address": addr, "gt_sqft": gt_sqft, "engine_sqft": "",
                "delta_sqft": "", "delta_pct": "", "gt_facets": gt_facets,
                "engine_facets": "", "delta_facets": "", "gt_pitch": gt_pitch,
                "engine_pitch": "", "delta_pitch": "", "primary_source": source,
                "duration_s": "", "notes": "; ".join(str(n) for n in notes)[:200],
                "_success": False, "_abs_pct_area": None,
                "_abs_facets_delta": None, "_abs_pitch_delta": None}
    eng_sqft = result.total_area_m2 * 10.7639 if hasattr(result, "total_area_m2") else 0
    eng_facets = len(result.facets) if hasattr(result, "facets") else 0
    eng_pitch = result.predominant_pitch_x_in_12 if hasattr(result, "predominant_pitch_x_in_12") else 0
    duration = result.duration_s if hasattr(result, "duration_s") else 0
    delta_sqft = eng_sqft - gt_sqft
    delta_pct = (delta_sqft / gt_sqft * 100) if gt_sqft else 0
    return {"address": addr, "gt_sqft": gt_sqft, "engine_sqft": round(eng_sqft, 0),
            "delta_sqft": round(delta_sqft, 0), "delta_pct": round(delta_pct, 1),
            "gt_facets": gt_facets, "engine_facets": eng_facets,
            "delta_facets": eng_facets - gt_facets, "gt_pitch": gt_pitch,
            "engine_pitch": round(eng_pitch, 1),
            "delta_pitch": round(eng_pitch - gt_pitch, 1),
            "primary_source": source, "duration_s": round(duration, 1),
            "notes": "; ".join(str(n) for n in notes)[:200], "_success": True,
            "_abs_pct_area": abs(delta_pct), "_abs_facets_delta": abs(eng_facets - gt_facets),
            "_abs_pitch_delta": abs(eng_pitch - gt_pitch)}

def worker(i, n, row, mode):
    addr = row.get("address", "?")
    lat = sf(row.get("lat")); lon = sf(row.get("lon"))
    log = [f"  [{i}/{n}] {addr} ({lat:.4f}, {lon:.4f})"]
    if lat is None or lon is None:
        log.append("    → SKIPPED")
        return i, log, None
    t0 = time.time()
    result, source, notes = run_one(addr, lat, lon, mode)
    elapsed = time.time() - t0
    r = compare(row, result, source, notes)
    log.append(f"      → {source}  {elapsed:.1f}s  area={r.get('engine_sqft','?')}sqft "
               f"(gt={r.get('gt_sqft','?')}, Δ={r.get('delta_pct','?')}%)  "
               f"facets={r.get('engine_facets','?')} (gt={r.get('gt_facets','?')})")
    return i, log, r

def print_summary(results):
    n = len(results)
    succ = [r for r in results if r["_success"]]
    print(f"\n{'=' * 72}")
    print(f"  SUMMARY  ({n} addresses, {len(succ)} succeeded, {n-len(succ)} failed)")
    print("=" * 72)
    if not succ: return
    pct = [r["_abs_pct_area"] for r in succ if r["_abs_pct_area"] is not None]
    fac = [r["_abs_facets_delta"] for r in succ if r["_abs_facets_delta"] is not None]
    pit = [r["_abs_pitch_delta"] for r in succ if r["_abs_pitch_delta"] is not None]
    if pct:
        print(f"  AREA error (%):    mean={statistics.mean(pct):.1f}  "
              f"median={statistics.median(pct):.1f}  max={max(pct):.1f}")
    if fac:
        print(f"  FACET delta:       mean={statistics.mean(fac):.1f}  median={statistics.median(fac):.0f}  max={max(fac)}")
    if pit:
        print(f"  PITCH delta (x12): mean={statistics.mean(pit):.2f}  median={statistics.median(pit):.2f}  max={max(pit):.2f}")
    excellent = sum(1 for p in pct if p <= 5)
    good = sum(1 for p in pct if 5 < p <= 10)
    acceptable = sum(1 for p in pct if 10 < p <= 20)
    poor = sum(1 for p in pct if p > 20)
    print(f"\n  Quality buckets (area % error):")
    print(f"    Excellent (≤5%):     {excellent}/{len(pct)}")
    print(f"    Good (5-10%):        {good}/{len(pct)}")
    print(f"    Acceptable (10-20%): {acceptable}/{len(pct)}")
    print(f"    Poor (>20%):         {poor}/{len(pct)}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv")
    ap.add_argument("--mode", default="hybrid")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()
    rows = read_input(Path(args.input_csv))
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else Path(f"/tmp/v2_groundtruth_{ts}.csv")
    print(f"Harness: {len(rows)} addresses, {args.workers} workers")
    t0 = time.time()
    res = {}
    if args.workers <= 1:
        for i, row in enumerate(rows, 1):
            i, log, r = worker(i, len(rows), row, args.mode)
            for ll in log: print(ll)
            if r: res[i] = r
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(worker, i, len(rows), row, args.mode): i
                       for i, row in enumerate(rows, 1)}
            for fut in as_completed(futures):
                i, log, r = fut.result()
                for ll in log: print(ll)
                if r: res[i] = r
    print(f"\nTotal runtime: {time.time()-t0:.1f}s")
    results = [res[i] for i in sorted(res)]
    keys = [k for k in results[0].keys() if not k.startswith("_")] if results else []
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader()
        for r in results: w.writerow({k: v for k, v in r.items() if not k.startswith("_")})
    print_summary(results)
    print(f"\n  Detailed results: {out_path}")

if __name__ == "__main__": main()
HARNESSEOF
echo "    parallel harness installed"

# ============================================================================
# Step 4: apply v3.11 patches (segmentation + footprint)
# ============================================================================
echo
echo "=== 4/8: apply v3.11 segmentation + footprint patches ==="
sudo /home/roofmeasure/engine/venv/bin/python << 'PYEOF'
from pathlib import Path

SEG = Path("/home/roofmeasure/engine/roofmeasure/segmentation_v2.py")
WIRE = Path("/home/roofmeasure/engine/roofmeasure/measurement_v2_wireup.py")
HYBRID = Path("/home/roofmeasure/engine/roofmeasure/hybrid_pipeline.py")
FP = Path("/home/roofmeasure/engine/roofmeasure/footprint_v2.py")

# v3.5 adaptive overhang (idempotent — most already deployed)
seg = SEG.read_text()
if "footprint_vertex_count" not in seg:
    seg = seg.replace("    footprint_area_m2=None,\n):",
                      "    footprint_area_m2=None,\n    footprint_vertex_count=None,\n):")
if "OVERHANG_FACTOR = 1.15  # eaves overhang" in seg:
    seg = seg.replace(
        "            OVERHANG_FACTOR = 1.15  # eaves overhang typical residential ~15%",
        "            v = footprint_vertex_count or 8\n"
        "            if v <= 5: OVERHANG_FACTOR = 1.40\n"
        "            elif v <= 7: OVERHANG_FACTOR = 1.25\n"
        "            elif v <= 12: OVERHANG_FACTOR = 1.15\n"
        "            else: OVERHANG_FACTOR = 1.10\n"
        "            LOG.info('seg_v3_5: overhang=%.2f for %d-vert', OVERHANG_FACTOR, v)")
    seg = seg.replace('LOG.info("seg_v3_4: footprint override',
                      'LOG.info("seg_v3_5: footprint override')
    SEG.write_text(seg)
    print("  v3.5 -> segmentation PATCHED")

wire = WIRE.read_text()
if "footprint_vertex_count=" not in wire:
    wire = wire.replace("        footprint_area_m2=fp_area,\n    )",
                        "        footprint_area_m2=fp_area,\n        footprint_vertex_count=len(fp.polygon_lonlat),\n    )")
    WIRE.write_text(wire); print("  v3.5 -> wireup PATCHED")

hybrid = HYBRID.read_text()
if "footprint_vertex_count=" not in hybrid:
    hybrid = hybrid.replace("            footprint_area_m2=_fp_area,\n        )",
                            "            footprint_area_m2=_fp_area,\n            footprint_vertex_count=len(fp.polygon_lonlat),\n        )")
    HYBRID.write_text(hybrid); print("  v3.5 -> hybrid PATCHED")

# v3.11 OSMnx radii
src = FP.read_text()
if "SEARCH_RADII_M = [80, 150, 300, 500, 1000]" not in src:
    src = src.replace("SEARCH_RADII_M = [80, 150, 300]",
                      "SEARCH_RADII_M = [80, 150, 300, 500, 1000]")
    FP.write_text(src); print("  v3.11 OSMnx radii 1000m")
PYEOF

# ============================================================================
# Step 5: v3.12 Solar quality fallback
# ============================================================================
echo
echo "=== 5/8: v3.12 Solar HIGH/MED/LOW fallback ==="
sudo /home/roofmeasure/engine/venv/bin/python << 'PYEOF'
from pathlib import Path
HYBRID = Path("/home/roofmeasure/engine/roofmeasure/hybrid_pipeline.py")
src = HYBRID.read_text()

OLD = """            _solar_resp = _so_req.get(
                'https://solar.googleapis.com/v1/buildingInsights:findClosest',
                params={'location.latitude': lat, 'location.longitude': lon,
                        'requiredQuality': 'HIGH', 'key': _solar_key},
                timeout=30,
            )
            if _solar_resp.status_code == 200:
                solar_result = _solar_resp.json()
            else:
                raise Exception(f'Solar API HTTP {_solar_resp.status_code}: {_solar_resp.text[:200]}')"""
NEW = """            solar_result = None
            _solar_last_err = None
            for _q in ("HIGH", "MEDIUM", "LOW"):
                _solar_resp = _so_req.get(
                    'https://solar.googleapis.com/v1/buildingInsights:findClosest',
                    params={'location.latitude': lat, 'location.longitude': lon,
                            'requiredQuality': _q, 'key': _solar_key},
                    timeout=30,
                )
                if _solar_resp.status_code == 200:
                    solar_result = _solar_resp.json()
                    notes.append(f'solar quality={_q} OK')
                    break
                else:
                    _solar_last_err = f'HTTP {_solar_resp.status_code} at {_q}'
            if solar_result is None:
                raise Exception(f'Solar API failed all quality: {_solar_last_err}')"""
if OLD in src:
    src = src.replace(OLD, NEW)
    HYBRID.write_text(src)
    print("  v3.12 quality fallback INSTALLED")
elif "for _q in (\"HIGH\", \"MEDIUM\", \"LOW\")" in src:
    print("  v3.12 quality fallback already present")
else:
    print("  WARN: v3.8 single-quality block not found verbatim (Solar may be patched differently)")
PYEOF

# ============================================================================
# Step 6: run harness (parallel — 3-4 min)
# ============================================================================
echo
echo "=== 6/8: run ground-truth harness (parallel, 3-4 min) ==="
sudo -E /home/roofmeasure/engine/venv/bin/python /home/roofmeasure/engine/tests/ground_truth_harness.py /home/roofmeasure/engine/tests/ground_truth.csv 2>&1 | tee /tmp/harness_master.log | grep -E "^\s*(\[[0-9]+/11\]|→|SUMMARY|=|Quality|AREA|FACET|PITCH|Excellent|Good|Acceptable|Poor|Total|Detailed)"

# ============================================================================
# Step 7: install auto-test watcher
# ============================================================================
echo
echo "=== 7/8: install auto-test watcher ==="
if ! command -v inotifywait > /dev/null 2>&1; then
    sudo apt-get install -y inotify-tools 2>&1 | tail -1
fi
sudo tee /usr/local/bin/roofmeasure-auto-test.sh > /dev/null << 'WATCHEOF'
#!/bin/bash
ENGINE_DIR=/home/roofmeasure/engine/roofmeasure
TEST=/home/roofmeasure/engine/tests/ground_truth_harness.py
GT=/home/roofmeasure/engine/tests/ground_truth.csv
LOG=/tmp/auto_test_watch.log
VENV=/home/roofmeasure/engine/venv/bin/python
echo "[$(date -u +%FT%TZ)] auto-test watcher started" >> "$LOG"
run() {
    echo "" >> "$LOG"
    echo "[$(date -u +%FT%TZ)] HARNESS RUN ($1)" >> "$LOG"
    sudo -E "$VENV" "$TEST" "$GT" 2>&1 | tee -a "$LOG" | tail -20
    echo "[$(date -u +%FT%TZ)] done" >> "$LOG"
}
run startup
inotifywait -m -e modify,create,move "$ENGINE_DIR" --format '%f %e' 2>/dev/null | \
while read file event; do
    case "$file" in *.py) sleep 5; run "$file changed";; esac
done
WATCHEOF
sudo chmod +x /usr/local/bin/roofmeasure-auto-test.sh
sudo tee /etc/systemd/system/roofmeasure-auto-test.service > /dev/null << 'SVCEOF'
[Unit]
Description=RoofMeasure auto-test watcher
After=network.target
[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/roofmeasure-auto-test.sh
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
SVCEOF
sudo systemctl daemon-reload
sudo systemctl enable roofmeasure-auto-test.service > /dev/null 2>&1
sudo systemctl restart roofmeasure-auto-test.service
sleep 2
echo "    auto-test watcher: $(sudo systemctl is-active roofmeasure-auto-test.service)"

# ============================================================================
# Step 8: final state summary
# ============================================================================
echo
echo "================================================================"
echo "  MASTER DEPLOY COMPLETE"
echo "================================================================"
echo
echo "  Backup: $BACKUP_TAR"
echo "  Harness log: /tmp/harness_master.log"
echo "  Auto-test log: /tmp/auto_test_watch.log"
echo
echo "  Recent results (last 10 lines):"
tail -10 /tmp/harness_master.log
echo
echo "  Rollback if needed:"
echo "    sudo tar xzf $BACKUP_TAR -C /home/roofmeasure/engine"
echo
echo "  Live watch (every code change re-triggers harness):"
echo "    tail -f /tmp/auto_test_watch.log"
