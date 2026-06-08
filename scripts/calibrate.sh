#!/bin/bash
# Parameter calibration — grid-search the overhang factors against ground truth.
#
# The v3.5 adaptive-overhang table was derived analytically from 3 data points:
#   5 verts → 1.40, 8-12 verts → 1.15, 13+ verts → 1.10
#
# This tool searches around those values to find the combination that
# minimizes mean error across all SUCCESSFUL ground-truth addresses.
#
# After running, paste the winning parameters into the engine and re-test.

OUT=/tmp/v3_calibration_$(date +%Y%m%d_%H%M%S)
mkdir -p "$OUT"
echo "Calibration output -> $OUT/"

sudo -E /home/roofmeasure/engine/venv/bin/python << PYEOF
import sys, csv, json, time, math, statistics
from pathlib import Path
sys.path.insert(0, "/home/roofmeasure/engine")

OUT = Path("$OUT")
GT_CSV = Path("/home/roofmeasure/engine/tests/ground_truth.csv")

# Search grid — overhang factor for each vertex-count bucket
GRID = {
    "verts_5": [1.30, 1.35, 1.40, 1.45, 1.50, 1.55],
    "verts_6_7": [1.15, 1.20, 1.25, 1.30, 1.35],
    "verts_8_12": [1.05, 1.10, 1.15, 1.20, 1.25],
    "verts_13_plus": [1.00, 1.05, 1.10, 1.15],
}

# Load ground truth
with open(GT_CSV) as f:
    rows = [r for r in csv.DictReader(f) if r["address"]]

# We need a way to call segment_roof with overridden overhang per address.
# Approach: temporarily monkey-patch the module-level adaptive table.

from roofmeasure import segmentation_v2 as seg_mod
from roofmeasure.hybrid_pipeline import measure_hybrid

# Backup the segment_roof function so we can re-import
ORIG_SEG = seg_mod.segment_roof
ORIG_SRC = (Path("/home/roofmeasure/engine/roofmeasure/segmentation_v2.py").read_text())

def patch_overhang(values):
    """Edit the v3.5 adaptive overhang lines in segmentation_v2.py."""
    src = ORIG_SRC
    OLD = "if v <= 5: OVERHANG_FACTOR = 1.40"
    NEW = f"if v <= 5: OVERHANG_FACTOR = {values['verts_5']}"
    src = src.replace(OLD, NEW)
    src = src.replace("elif v <= 7: OVERHANG_FACTOR = 1.25",
                      f"elif v <= 7: OVERHANG_FACTOR = {values['verts_6_7']}")
    src = src.replace("elif v <= 12: OVERHANG_FACTOR = 1.15",
                      f"elif v <= 12: OVERHANG_FACTOR = {values['verts_8_12']}")
    src = src.replace("else: OVERHANG_FACTOR = 1.10",
                      f"else: OVERHANG_FACTOR = {values['verts_13_plus']}")
    Path("/home/roofmeasure/engine/roofmeasure/segmentation_v2.py").write_text(src)
    # Re-import
    import importlib
    importlib.reload(seg_mod)
    from roofmeasure import hybrid_pipeline
    importlib.reload(hybrid_pipeline)
    return hybrid_pipeline.measure_hybrid

def restore():
    Path("/home/roofmeasure/engine/roofmeasure/segmentation_v2.py").write_text(ORIG_SRC)

def evaluate(values):
    """Run measure_hybrid for each row, return mean absolute % area error."""
    fn = patch_overhang(values)
    errors = []
    per_addr = []
    for row in rows:
        try:
            lat = float(row["lat"]); lon = float(row["lon"])
            gt_sqft = float(row["gt_total_sqft"])
        except (ValueError, KeyError):
            continue
        r = fn(lat, lon)
        if not r.success:
            per_addr.append((row["address"], "fail", None))
            continue
        eng_sqft = r.total_area_m2 * 10.7639
        if gt_sqft > 0:
            err = (eng_sqft - gt_sqft) / gt_sqft * 100
            errors.append(abs(err))
            per_addr.append((row["address"], round(eng_sqft, 0), round(err, 1)))
    return errors, per_addr

# Iterate the grid
import itertools
combos = list(itertools.product(GRID["verts_5"], GRID["verts_6_7"], GRID["verts_8_12"], GRID["verts_13_plus"]))
print(f"Grid size: {len(combos)} combinations")
print(f"Running each across {len(rows)} addresses (only addresses with cached data run fast)")
print()
print("WARNING: full grid is impractical (~12 min × N combos). Sampling subset.")
SUBSAMPLE = 16  # try 16 random combinations to keep runtime manageable
import random
random.seed(42)
sampled = random.sample(combos, min(SUBSAMPLE, len(combos)))

results = []
for i, (v5, v67, v812, v13) in enumerate(sampled, 1):
    cfg = {"verts_5": v5, "verts_6_7": v67, "verts_8_12": v812, "verts_13_plus": v13}
    print(f"\n[{i}/{len(sampled)}] testing {cfg}")
    t0 = time.time()
    try:
        errors, per_addr = evaluate(cfg)
        mean_err = statistics.mean(errors) if errors else float("inf")
        median_err = statistics.median(errors) if errors else float("inf")
        n_succ = len(errors)
    except Exception as e:
        print(f"  ERR {e}")
        mean_err = median_err = float("inf")
        n_succ = 0
        per_addr = []
    elapsed = time.time() - t0
    print(f"  n_success={n_succ}, mean_err={mean_err:.2f}%, median={median_err:.2f}%  ({elapsed:.0f}s)")
    results.append({"cfg": cfg, "n_success": n_succ, "mean_err": mean_err, "median_err": median_err, "elapsed": elapsed, "per_addr": per_addr})

restore()

# Sort by mean error
results.sort(key=lambda r: (r["mean_err"], -r["n_success"]))

print("\n\n=== TOP 5 CONFIGURATIONS ===")
for r in results[:5]:
    print(f"  mean_err={r['mean_err']:.2f}% median={r['median_err']:.2f}% n_success={r['n_success']}")
    print(f"    cfg: {r['cfg']}")

# Save full results
with open(OUT / "calibration.json", "w") as f:
    json.dump([{
        "cfg": r["cfg"], "n_success": r["n_success"],
        "mean_err": r["mean_err"], "median_err": r["median_err"],
        "elapsed": r["elapsed"],
    } for r in results], f, indent=2)

print(f"\nFull results in {OUT}/calibration.json")
print("Original segmentation_v2.py restored.")
PYEOF
