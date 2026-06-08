"""EagleView ground-truth comparison harness.

Read a CSV of ground-truth measurements (from EagleView PDFs), run the v2.1
hybrid pipeline on each, output a comparison CSV with deltas.

The "EagleView quality" metric Bill keeps invoking — once we have 10-20
ground-truth rows, this script tells us numerically how close we are.

USAGE
-----
1. Compile EagleView truth into /home/roofmeasure/engine/tests/ground_truth.csv
   with these columns (header row required):

       address, lat, lon, gt_total_sqft, gt_num_facets, gt_pitch_x12, gt_predominant_pitch_deg

   Example row:
       "123 Main St Bedford TX",32.829621,-97.152498,3613,7,6.5,28.4

2. Run on the VPS:

       sudo /home/roofmeasure/engine/venv/bin/python \\
            /home/roofmeasure/engine/tests/ground_truth_harness.py \\
            /home/roofmeasure/engine/tests/ground_truth.csv

3. Outputs go to /tmp/v2_groundtruth_<timestamp>.csv plus a summary on stdout.

OUTPUT CSV COLUMNS
------------------
    address, gt_sqft, engine_sqft, delta_sqft, delta_pct,
    gt_facets, engine_facets, delta_facets,
    gt_pitch, engine_pitch, delta_pitch,
    primary_source, duration_s, notes

SUMMARY STATS
-------------
    n_runs, n_succeeded, n_failed
    mean_abs_pct_error_area, p50, p90, max
    mean_abs_facet_count_delta
    mean_abs_pitch_delta_x12
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import logging
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/roofmeasure/engine")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("groundtruth")

REQUIRED_COLS = {
    "address", "lat", "lon",
    "gt_total_sqft", "gt_num_facets", "gt_pitch_x12",
}


def read_input(path: Path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        raise SystemExit(f"empty CSV: {path}")
    cols = set(rows[0].keys())
    missing = REQUIRED_COLS - cols
    if missing:
        raise SystemExit(f"missing required columns: {sorted(missing)}")
    return rows


def safe_float(s, default=None):
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return default


def safe_int(s, default=None):
    try:
        return int(float(str(s).strip()))
    except (TypeError, ValueError):
        return default


def run_one(addr: str, lat: float, lon: float, mode: str):
    """Run the engine on one address. mode = 'hybrid' | 'lidar_only'."""
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
        return {
            "address": addr,
            "gt_sqft": gt_sqft, "engine_sqft": "", "delta_sqft": "", "delta_pct": "",
            "gt_facets": gt_facets, "engine_facets": "", "delta_facets": "",
            "gt_pitch": gt_pitch, "engine_pitch": "", "delta_pitch": "",
            "primary_source": source,
            "duration_s": "",
            "notes": "; ".join(str(n) for n in notes),
            "_success": False,
            "_abs_pct_area": None,
            "_abs_facets_delta": None,
            "_abs_pitch_delta": None,
        }

    eng_sqft = engine_result.total_area_m2 * 10.7639 if hasattr(engine_result, "total_area_m2") else 0
    eng_facets = len(engine_result.facets) if hasattr(engine_result, "facets") else 0
    eng_pitch = (
        engine_result.predominant_pitch_x_in_12
        if hasattr(engine_result, "predominant_pitch_x_in_12")
        else 0
    )
    duration = engine_result.duration_s if hasattr(engine_result, "duration_s") else 0

    delta_sqft = eng_sqft - gt_sqft
    delta_pct = (delta_sqft / gt_sqft * 100) if gt_sqft else 0
    delta_facets = eng_facets - gt_facets
    delta_pitch = eng_pitch - gt_pitch

    return {
        "address": addr,
        "gt_sqft": gt_sqft,
        "engine_sqft": round(eng_sqft, 0),
        "delta_sqft": round(delta_sqft, 0),
        "delta_pct": round(delta_pct, 1),
        "gt_facets": gt_facets,
        "engine_facets": eng_facets,
        "delta_facets": delta_facets,
        "gt_pitch": gt_pitch,
        "engine_pitch": round(eng_pitch, 1),
        "delta_pitch": round(delta_pitch, 1),
        "primary_source": source,
        "duration_s": round(duration, 1),
        "notes": "; ".join(str(n) for n in notes)[:200],
        "_success": True,
        "_abs_pct_area": abs(delta_pct),
        "_abs_facets_delta": abs(delta_facets),
        "_abs_pitch_delta": abs(delta_pitch),
    }


def print_summary(results):
    n = len(results)
    succeeded = [r for r in results if r["_success"]]
    failed = n - len(succeeded)

    print(f"\n{'=' * 72}")
    print(f"  SUMMARY  ({n} addresses, {len(succeeded)} succeeded, {failed} failed)")
    print("=" * 72)

    if not succeeded:
        print("  No successful runs to summarize.")
        return

    pct_errors = [r["_abs_pct_area"] for r in succeeded if r["_abs_pct_area"] is not None]
    facets_deltas = [r["_abs_facets_delta"] for r in succeeded if r["_abs_facets_delta"] is not None]
    pitch_deltas = [r["_abs_pitch_delta"] for r in succeeded if r["_abs_pitch_delta"] is not None]

    if pct_errors:
        print(f"  AREA error (%):    mean={statistics.mean(pct_errors):.1f}  "
              f"median={statistics.median(pct_errors):.1f}  "
              f"max={max(pct_errors):.1f}")
    if facets_deltas:
        print(f"  FACET count delta: mean={statistics.mean(facets_deltas):.1f}  "
              f"median={statistics.median(facets_deltas):.0f}  "
              f"max={max(facets_deltas)}")
    if pitch_deltas:
        print(f"  PITCH delta (x12): mean={statistics.mean(pitch_deltas):.2f}  "
              f"median={statistics.median(pitch_deltas):.2f}  "
              f"max={max(pitch_deltas):.2f}")

    # EagleView quality buckets
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
    ap.add_argument("input_csv", help="Ground-truth CSV (see header docstring)")
    ap.add_argument("--mode", default="hybrid", choices=["hybrid", "lidar_only"],
                    help="Which pipeline to test")
    ap.add_argument("--output", default=None, help="Output CSV path")
    args = ap.parse_args()

    rows = read_input(Path(args.input_csv))
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else Path(f"/tmp/v2_groundtruth_{ts}.csv")

    results = []
    for i, row in enumerate(rows, 1):
        addr = row.get("address", "?")
        lat = safe_float(row.get("lat"))
        lon = safe_float(row.get("lon"))
        if lat is None or lon is None:
            print(f"  [{i}/{len(rows)}] {addr}: SKIPPED (bad lat/lon)")
            continue
        print(f"  [{i}/{len(rows)}] {addr} ({lat:.4f}, {lon:.4f})")
        t0 = time.time()
        engine_result, source, notes = run_one(addr, lat, lon, args.mode)
        elapsed = time.time() - t0
        r = compare(row, engine_result, source, notes)
        print(f"      → {source}  {elapsed:.1f}s  "
              f"area={r.get('engine_sqft','?')}sqft (gt={r.get('gt_sqft','?')}, "
              f"Δ={r.get('delta_pct','?')}%)  "
              f"facets={r.get('engine_facets','?')} (gt={r.get('gt_facets','?')})")
        results.append(r)

    # Write CSV (excluding _internal keys)
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
