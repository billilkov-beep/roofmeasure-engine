#!/bin/bash
# Install parallel harness — runs the 11 ground-truth addresses concurrently
# (4 workers by default), dropping runtime from ~12 min to ~3-4 min.
#
# Useful because the no-OSM US addresses each cycle through 3 OSMnx radii ×
# 3 Overpass mirrors × 30s timeout = 90s+ each, serially. With 4 workers
# those run in parallel and total runtime is dominated by the slowest single
# address.
#
# Replaces /home/roofmeasure/engine/tests/ground_truth_harness.py.
# Existing serial behavior preserved via --workers=1 flag.

sudo tee /home/roofmeasure/engine/tests/ground_truth_harness.py > /dev/null << 'HARNESSEOF'
"""Ground-truth harness — parallel version.

Runs measure_hybrid against each row of a ground-truth CSV concurrently,
compares results to EagleView numbers, prints summary + per-row deltas.

USAGE
-----
    sudo /home/roofmeasure/engine/venv/bin/python \\
         /home/roofmeasure/engine/tests/ground_truth_harness.py \\
         /home/roofmeasure/engine/tests/ground_truth.csv \\
         [--workers=4] [--mode=hybrid]
"""
from __future__ import annotations

import argparse, csv, datetime, logging, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, "/home/roofmeasure/engine")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("groundtruth")

REQUIRED_COLS = {"address", "lat", "lon",
                 "gt_total_sqft", "gt_num_facets", "gt_pitch_x12"}


def read_input(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"empty CSV: {path}")
    cols = set(rows[0].keys())
    missing = REQUIRED_COLS - cols
    if missing:
        raise SystemExit(f"missing required columns: {sorted(missing)}")
    return rows


def safe_float(s, default=None):
    try: return float(str(s).strip())
    except (TypeError, ValueError): return default


def safe_int(s, default=None):
    try: return int(float(str(s).strip()))
    except (TypeError, ValueError): return default


def run_one(addr, lat, lon, mode):
    try:
        if mode == "lidar_only":
            from roofmeasure.measurement_v2_wireup import measure_via_lidar_v2
            r = measure_via_lidar_v2(lat, lon)
            if r is None or not r.success:
                return None, "lidar_v2: failed", []
            return r, r.source, r.notes
        else:
            from roofmeasure.hybrid_pipeline import measure_hybrid
            r = measure_hybrid(lat, lon)
            if not r.success:
                return None, "hybrid: failed", r.notes
            return r, r.primary_source, r.notes
    except Exception as e:
        LOG.exception("run_one exception")
        return None, f"exception: {e}", []


def compare(row, engine_result, source, notes):
    addr = row.get("address", "?")
    gt_sqft = safe_float(row.get("gt_total_sqft"), 0)
    gt_facets = safe_int(row.get("gt_num_facets"), 0)
    gt_pitch = safe_float(row.get("gt_pitch_x12"), 0)
    if engine_result is None:
        return {"address": addr, "gt_sqft": gt_sqft,
                "engine_sqft": "", "delta_sqft": "", "delta_pct": "",
                "gt_facets": gt_facets, "engine_facets": "", "delta_facets": "",
                "gt_pitch": gt_pitch, "engine_pitch": "", "delta_pitch": "",
                "primary_source": source, "duration_s": "",
                "notes": "; ".join(str(n) for n in notes)[:200],
                "_success": False, "_abs_pct_area": None,
                "_abs_facets_delta": None, "_abs_pitch_delta": None}
    eng_sqft = engine_result.total_area_m2 * 10.7639 if hasattr(engine_result, "total_area_m2") else 0
    eng_facets = len(engine_result.facets) if hasattr(engine_result, "facets") else 0
    eng_pitch = engine_result.predominant_pitch_x_in_12 if hasattr(engine_result, "predominant_pitch_x_in_12") else 0
    duration = engine_result.duration_s if hasattr(engine_result, "duration_s") else 0
    delta_sqft = eng_sqft - gt_sqft
    delta_pct = (delta_sqft / gt_sqft * 100) if gt_sqft else 0
    return {
        "address": addr, "gt_sqft": gt_sqft,
        "engine_sqft": round(eng_sqft, 0), "delta_sqft": round(delta_sqft, 0),
        "delta_pct": round(delta_pct, 1),
        "gt_facets": gt_facets, "engine_facets": eng_facets,
        "delta_facets": eng_facets - gt_facets,
        "gt_pitch": gt_pitch, "engine_pitch": round(eng_pitch, 1),
        "delta_pitch": round(eng_pitch - gt_pitch, 1),
        "primary_source": source, "duration_s": round(duration, 1),
        "notes": "; ".join(str(n) for n in notes)[:200],
        "_success": True, "_abs_pct_area": abs(delta_pct),
        "_abs_facets_delta": abs(eng_facets - gt_facets),
        "_abs_pitch_delta": abs(eng_pitch - gt_pitch),
    }


def worker(i, n_total, row, mode):
    """Process a single row. Returns (i, log_lines, result_dict)."""
    addr = row.get("address", "?")
    lat = safe_float(row.get("lat"))
    lon = safe_float(row.get("lon"))
    log = []
    log.append(f"  [{i}/{n_total}] {addr} ({lat:.4f}, {lon:.4f})")
    if lat is None or lon is None:
        log.append("    → SKIPPED (bad lat/lon)")
        return i, log, None
    t0 = time.time()
    engine_result, source, notes = run_one(addr, lat, lon, mode)
    elapsed = time.time() - t0
    r = compare(row, engine_result, source, notes)
    log.append(f"      → {source}  {elapsed:.1f}s  "
               f"area={r.get('engine_sqft','?')}sqft (gt={r.get('gt_sqft','?')}, "
               f"Δ={r.get('delta_pct','?')}%)  "
               f"facets={r.get('engine_facets','?')} (gt={r.get('gt_facets','?')})")
    return i, log, r


def print_summary(results):
    n = len(results)
    succeeded = [r for r in results if r["_success"]]
    failed = n - len(succeeded)
    print(f"\n{'=' * 72}")
    print(f"  SUMMARY  ({n} addresses, {len(succeeded)} succeeded, {failed} failed)")
    print("=" * 72)
    if not succeeded:
        print("  No successful runs.")
        return
    pct_errors = [r["_abs_pct_area"] for r in succeeded if r["_abs_pct_area"] is not None]
    facets_d = [r["_abs_facets_delta"] for r in succeeded if r["_abs_facets_delta"] is not None]
    pitch_d = [r["_abs_pitch_delta"] for r in succeeded if r["_abs_pitch_delta"] is not None]
    if pct_errors:
        print(f"  AREA error (%):    mean={statistics.mean(pct_errors):.1f}  "
              f"median={statistics.median(pct_errors):.1f}  max={max(pct_errors):.1f}")
    if facets_d:
        print(f"  FACET delta:       mean={statistics.mean(facets_d):.1f}  "
              f"median={statistics.median(facets_d):.0f}  max={max(facets_d)}")
    if pitch_d:
        print(f"  PITCH delta (x12): mean={statistics.mean(pitch_d):.2f}  "
              f"median={statistics.median(pitch_d):.2f}  max={max(pitch_d):.2f}")
    print()
    excellent = sum(1 for p in pct_errors if p <= 5)
    good = sum(1 for p in pct_errors if 5 < p <= 10)
    acceptable = sum(1 for p in pct_errors if 10 < p <= 20)
    poor = sum(1 for p in pct_errors if p > 20)
    print(f"  Quality buckets (area % error):")
    print(f"    Excellent (≤5%):    {excellent}/{len(pct_errors)}")
    print(f"    Good (5-10%):       {good}/{len(pct_errors)}")
    print(f"    Acceptable (10-20%): {acceptable}/{len(pct_errors)}")
    print(f"    Poor (>20%):        {poor}/{len(pct_errors)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv")
    ap.add_argument("--mode", default="hybrid", choices=["hybrid", "lidar_only"])
    ap.add_argument("--workers", type=int, default=4,
                    help="Concurrent workers (default 4; 1 = serial)")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    rows = read_input(Path(args.input_csv))
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else Path(f"/tmp/v2_groundtruth_{ts}.csv")

    print(f"Harness running {len(rows)} addresses with {args.workers} workers...")
    t0 = time.time()
    results_by_i = {}
    if args.workers <= 1:
        # Serial mode — preserves original behavior
        for i, row in enumerate(rows, 1):
            i, log, r = worker(i, len(rows), row, args.mode)
            for line in log: print(line)
            if r is not None: results_by_i[i] = r
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(worker, i, len(rows), row, args.mode): i
                       for i, row in enumerate(rows, 1)}
            for fut in as_completed(futures):
                i, log, r = fut.result()
                for line in log: print(line)
                if r is not None: results_by_i[i] = r
    elapsed = time.time() - t0
    print(f"\nTotal runtime: {elapsed:.1f}s")

    # Output in original CSV order
    results = [results_by_i[i] for i in sorted(results_by_i)]
    public_keys = [k for k in results[0].keys() if not k.startswith("_")] if results else []
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=public_keys)
        w.writeheader()
        for r in results:
            w.writerow({k: v for k, v in r.items() if not k.startswith("_")})

    print_summary(results)
    print(f"\n  Detailed results: {out_path}")


if __name__ == "__main__":
    main()
HARNESSEOF

echo "Parallel harness installed."
echo
echo "Default 4 workers. Override with --workers=N. Serial: --workers=1."
echo
echo "Quick test (4 workers, all 11 addresses, expect ~3-4 min):"
echo "  sudo -E /home/roofmeasure/engine/venv/bin/python \\"
echo "       /home/roofmeasure/engine/tests/ground_truth_harness.py \\"
echo "       /home/roofmeasure/engine/tests/ground_truth.csv"
