"""Diagnostic dumper — runs the v2.1 pipeline on Bedford and writes intermediate state.

Outputs everything to /tmp/v2_debug/ so we can post-mortem any segmentation issue:
    footprint.geojson         — building footprint polygon
    raw_points.npy            — (N, 3) east/north/elev meters after LIDAR fetch
    raw_points.csv            — same as above, human-readable
    laz_classifications.npy   — LAS classification per point (0..255)
    z_stats.json              — z-range, unit detected, density
    seg_facets.json           — facets found by default segmentation
    seg_sweep.csv             — facet counts across multiple parameter combinations

Run on VPS:
    sudo /home/roofmeasure/engine/venv/bin/python /home/roofmeasure/engine/tests/dump_v2_intermediate.py
"""
from __future__ import annotations

import csv
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/roofmeasure/engine")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("dump")

OUT = Path("/tmp/v2_debug")
OUT.mkdir(parents=True, exist_ok=True)


def banner(s):
    print("\n" + "=" * 72)
    print(f"  {s}")
    print("=" * 72)


def main():
    from roofmeasure.footprint_v2 import get_building_footprint
    from roofmeasure.lidar_v2_raw import fetch_lidar_for_footprint
    from roofmeasure.segmentation_v2 import segment_roof, estimate_density

    LAT, LON = 32.829621, -97.152498  # Bedford TX

    banner("1. Footprint")
    fp = get_building_footprint(LAT, LON)
    if fp is None:
        LOG.error("No footprint — aborting")
        return
    print(f"  source={fp.source}, vertices={len(fp.polygon_lonlat)}")
    # Save footprint as GeoJSON
    geo = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[lo, la] for lo, la in fp.polygon_lonlat]],
        },
        "properties": {"source": fp.source, "osm_id": str(fp.osm_id or "")},
    }
    (OUT / "footprint.geojson").write_text(json.dumps(geo, indent=2))

    banner("2. LIDAR fetch (with polygon clip + LAS classifications)")
    crop = fetch_lidar_for_footprint(fp)
    if crop is None:
        LOG.error("No LIDAR — aborting (this would be expected for Canada)")
        return
    print(f"  source={crop.source}, n={len(crop.points_local_m)}, "
          f"density={crop.point_density_per_m2:.2f}/m^2")
    print(f"  z_unit={crop.z_unit_detected}, classes_used={crop.classifications_used}")
    print(f"  raw_point_count_in_file={crop.raw_point_count}, year={crop.captured_year}")

    pts = crop.points_local_m
    np.save(OUT / "raw_points.npy", pts)
    with open(OUT / "raw_points.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["east_m", "north_m", "elev_m"])
        for p in pts:
            w.writerow([f"{p[0]:.3f}", f"{p[1]:.3f}", f"{p[2]:.3f}"])

    z_stats = {
        "n_points": int(len(pts)),
        "density_per_m2": float(crop.point_density_per_m2 or 0),
        "z_unit": crop.z_unit_detected,
        "classifications_used": crop.classifications_used,
        "x_range": [float(pts[:, 0].min()), float(pts[:, 0].max())],
        "y_range": [float(pts[:, 1].min()), float(pts[:, 1].max())],
        "z_range": [float(pts[:, 2].min()), float(pts[:, 2].max())],
        "z_p1": float(np.percentile(pts[:, 2], 1)),
        "z_p50": float(np.percentile(pts[:, 2], 50)),
        "z_p99": float(np.percentile(pts[:, 2], 99)),
    }
    (OUT / "z_stats.json").write_text(json.dumps(z_stats, indent=2))
    print("  z_stats:", json.dumps(z_stats, indent=2))

    banner("3. Default segmentation (density-adaptive)")
    seg = segment_roof(
        pts,
        density_hint=crop.point_density_per_m2,
        points_already_filtered=crop.classifications_used,
        verbose=True,
    )
    print(f"  facets: {len(seg.facets)}")
    for f in seg.facets[:10]:
        sqft = f.area_m2 * 10.7639
        print(f"    #{f.id}: area={f.area_m2:.1f}m^2 ({sqft:.0f}sqft) "
              f"pitch={f.pitch_x_in_12:.1f}/12 az={f.azimuth_deg:.0f}deg")
    for n in seg.notes:
        print(f"  note: {n}")

    facets_json = [
        {
            "id": f.id, "area_m2": f.area_m2,
            "area_sqft": f.area_m2 * 10.7639,
            "pitch_x_in_12": f.pitch_x_in_12,
            "pitch_deg": f.pitch_deg,
            "azimuth_deg": f.azimuth_deg,
            "normal": list(f.plane_normal),
        }
        for f in seg.facets
    ]
    (OUT / "seg_facets.json").write_text(json.dumps(facets_json, indent=2))

    banner("4. Parameter sweep")
    print("Trying different (threshold, min_inliers, outlier_filter) combos")
    sweep_results = []
    for thr in (0.15, 0.20, 0.30, 0.40, 0.50):
        for mi in (10, 15, 25, 30):
            for of in (False, True):
                t0 = time.time()
                seg = segment_roof(
                    pts,
                    density_hint=crop.point_density_per_m2,
                    points_already_filtered=crop.classifications_used,
                    plane_threshold_m=thr,
                    plane_min_inliers=mi,
                    use_outlier_filter=of,
                )
                elapsed = time.time() - t0
                total_area = sum(f.area_m2 for f in seg.facets)
                row = {
                    "threshold_m": thr, "min_inliers": mi, "outlier": of,
                    "n_facets": len(seg.facets),
                    "total_area_m2": round(total_area, 1),
                    "total_area_sqft": round(total_area * 10.7639, 0),
                    "duration_s": round(elapsed, 2),
                }
                sweep_results.append(row)
                print(f"  thr={thr:.2f} min_inl={mi:2d} out={str(of):5s}  -> "
                      f"facets={len(seg.facets):2d}  area={total_area * 10.7639:5.0f}sqft  "
                      f"({elapsed:.1f}s)")

    with open(OUT / "seg_sweep.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sweep_results[0].keys()))
        w.writeheader()
        for r in sweep_results:
            w.writerow(r)

    banner("DONE")
    print(f"\nAll outputs in {OUT}/:")
    for p in sorted(OUT.iterdir()):
        print(f"  {p.name}  ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
