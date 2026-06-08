"""V3 smoke test — exercises the v2.1 modules + hybrid pipeline + edges.

Same two addresses as before, plus a parameter sweep summary.

Expected outcomes:
  Bedford TX:
    - footprint via OSM
    - lidar_v2_raw: 4000+ points (raw LAZ), z_unit detected
    - segmentation_v2_1: density-adaptive, expects 4-10 facets at 4.9 pts/m²
    - edge_classification: ~ridges, hips, eaves, rakes
    - hybrid_pipeline: primary='lidar' or 'lidar+solar'

  Whitby ON:
    - footprint via OSM
    - lidar_v2_raw: None (no USGS Canada)
    - ontario_lidar_provider: tries LIO SOLiDAR (URL may need verification)
    - hybrid_pipeline: primary='solar' (or 'none' if Solar unavailable)
"""
from __future__ import annotations

import logging
import sys
import time

sys.path.insert(0, "/home/roofmeasure/engine")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def banner(s):
    print("\n" + "=" * 72)
    print(f"  {s}")
    print("=" * 72)


def test_one(label, lat, lon):
    banner(f"FOOTPRINT — {label}")
    from roofmeasure.footprint_v2 import get_building_footprint
    fp = get_building_footprint(lat, lon)
    if fp is None:
        print(f"  no footprint at ({lat}, {lon})")
        return
    print(f"  source={fp.source}, vertices={len(fp.polygon_lonlat)}")

    banner(f"LIDAR (USGS 3DEP) — {label}")
    from roofmeasure.lidar_v2_raw import fetch_lidar_for_footprint
    t = time.time()
    crop = fetch_lidar_for_footprint(fp)
    if crop is None:
        print(f"  no USGS LIDAR ({time.time()-t:.1f}s) — expected for Canada")
    else:
        print(f"  OK in {time.time()-t:.1f}s")
        print(f"  n={len(crop.points_local_m)}, density={crop.point_density_per_m2:.1f}/m^2")
        print(f"  z_unit={crop.z_unit_detected}, classes_used={crop.classifications_used}")
        print(f"  year={crop.captured_year}, raw_total={crop.raw_point_count}")

    banner(f"ONTARIO LIDAR — {label}")
    try:
        from roofmeasure.ontario_lidar_provider import fetch_lidar_for_footprint as on_lidar
        t = time.time()
        on_crop = on_lidar(fp)
        if on_crop is None:
            print(f"  no Ontario LIDAR ({time.time()-t:.1f}s)")
        else:
            print(f"  OK in {time.time()-t:.1f}s, n={len(on_crop.points_local_m)} pts")
    except Exception as e:
        print(f"  ontario provider error: {e}")

    banner(f"SEGMENTATION (v2.1 density-adaptive) — {label}")
    if crop is not None:
        from roofmeasure.segmentation_v2 import segment_roof
        t = time.time()
        seg = segment_roof(
            crop.points_local_m,
            density_hint=crop.point_density_per_m2,
            points_already_filtered=crop.classifications_used,
        )
        print(f"  OK in {time.time()-t:.1f}s, facets={len(seg.facets)}")
        for f in seg.facets[:8]:
            sqft = f.area_m2 * 10.7639
            print(f"    #{f.id}: area={f.area_m2:.1f}m^2 ({sqft:.0f}sqft) "
                  f"pitch={f.pitch_x_in_12:.1f}/12 az={f.azimuth_deg:.0f}deg")
        for n in seg.notes:
            print(f"  note: {n}")
        if seg.facets:
            banner(f"EDGES (Phase 2) — {label}")
            from roofmeasure.edge_classification import classify_edges, aggregate_lengths
            t = time.time()
            edges = classify_edges(seg.facets, crop.points_local_m,
                                   ground_z=seg.ground_z,
                                   crs_origin_lonlat=crop.crs_origin_lonlat)
            print(f"  classified {len(edges)} edges in {time.time()-t:.1f}s")
            sums = aggregate_lengths(edges)
            for k, v in sums.items():
                if k.endswith("_ft"):
                    print(f"    {k}: {v}")
    else:
        print("  skipped (no LIDAR)")

    banner(f"HYBRID PIPELINE — {label}")
    try:
        from roofmeasure.hybrid_pipeline import measure_hybrid
        t = time.time()
        h = measure_hybrid(lat, lon)
        print(f"  done in {time.time()-t:.1f}s, success={h.success}, "
              f"primary={h.primary_source}")
        if h.success:
            print(f"  total: {h.total_area_m2:.1f}m^2 "
                  f"({h.total_area_m2*10.7639:.0f}sqft)")
            print(f"  facets: {len(h.facets)}")
            print(f"  predominant pitch: {h.predominant_pitch_x_in_12:.1f}/12")
        for n in h.notes[-6:]:
            print(f"  note: {n}")
    except Exception as e:
        print(f"  hybrid pipeline error: {e}")


def main():
    for label, lat, lon in [
        ("Bedford TX", 32.829621, -97.152498),
        ("Whitby ON", 43.907925, -78.971892),
    ]:
        test_one(label, lat, lon)

    banner("V3 SMOKE TEST COMPLETE")


if __name__ == "__main__":
    main()
