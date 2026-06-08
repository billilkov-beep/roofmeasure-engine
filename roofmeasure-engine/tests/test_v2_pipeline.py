"""Smoke test for the v2 pipeline (raw LAZ + robust footprint).

Run after deploying the v2 modules:
    sudo /home/roofmeasure/engine/venv/bin/python \
         /home/roofmeasure/engine/tests/test_v2_pipeline.py

Tests addresses one US (with USGS LIDAR coverage) and one CA (no coverage,
falls through to MS Canadian Footprints + Solar API at the orchestrator level).

Expected outcomes:
  Bedford TX:
    - footprint_v2: OK via OSMnx
    - lidar_v2_raw: OK with thousands of raw points
    - segmentation_v2: 4-10 facets

  Whitby ON:
    - footprint_v2: OK (OSMnx → Overpass-direct → MS-CA fallback chain)
    - lidar_v2_raw: None (no USGS 3DEP coverage in Canada — expected)
    - segmentation_v2: skipped
"""
from __future__ import annotations

import logging
import sys
import time

# Add engine root to path so we can import roofmeasure.*
sys.path.insert(0, "/home/roofmeasure/engine")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("smoke")


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def test_footprint(lat, lon, label):
    banner(f"footprint_v2 — {label} ({lat}, {lon})")
    try:
        from roofmeasure.footprint_v2 import get_building_footprint
    except ImportError as e:
        print(f"  IMPORT FAILED: {e}")
        return None
    t = time.time()
    fp = get_building_footprint(lat, lon)
    elapsed = time.time() - t
    if fp is None:
        print(f"  NO FOOTPRINT (after {elapsed:.1f}s)")
        return None
    print(f"  OK in {elapsed:.1f}s")
    print(f"  source: {fp.source}")
    print(f"  vertices: {len(fp.polygon_lonlat)}")
    print(f"  centroid: {fp.centroid_lonlat}")
    print(f"  osm_id: {fp.osm_id}")
    return fp


def test_lidar(fp, label):
    banner(f"lidar_v2_raw — {label}")
    if fp is None:
        print("  skipped (no footprint)")
        return None
    try:
        from roofmeasure.lidar_v2_raw import fetch_lidar_for_footprint
    except ImportError as e:
        print(f"  IMPORT FAILED: {e}")
        return None
    t = time.time()
    crop = fetch_lidar_for_footprint(fp)
    elapsed = time.time() - t
    if crop is None:
        print(f"  NO LIDAR (after {elapsed:.1f}s) — expected for Canada / out-of-coverage")
        return None
    print(f"  OK in {elapsed:.1f}s")
    print(f"  points: {len(crop.points_local_m)}")
    print(f"  source: {crop.source}")
    print(f"  tile: {crop.source_tile}")
    print(f"  density: {crop.point_density_per_m2:.1f} pts/m^2")
    print(f"  captured_year: {crop.captured_year}")
    return crop


def test_segmentation(crop, label):
    banner(f"segmentation_v2 — {label}")
    if crop is None:
        print("  skipped (no LIDAR points)")
        return
    try:
        from roofmeasure.segmentation_v2 import segment_roof
    except ImportError as e:
        print(f"  IMPORT FAILED: {e}")
        return
    t = time.time()
    result = segment_roof(crop.points_local_m)
    elapsed = time.time() - t
    print(f"  OK in {elapsed:.1f}s")
    print(f"  facets: {len(result.facets)}")
    print(f"  ground_z: {result.ground_z:.2f}m")
    for f in result.facets[:8]:
        sqft = f.area_m2 * 10.7639
        print(
            f"    facet #{f.id}: area={f.area_m2:.1f}m^2 ({sqft:.0f}sqft), "
            f"pitch={f.pitch_deg:.1f}deg ({f.pitch_x_in_12:.1f}/12), "
            f"azimuth={f.azimuth_deg:.0f}deg"
        )
    total = sum(f.area_m2 for f in result.facets)
    print(f"  TOTAL area: {total:.1f}m^2 ({total * 10.7639:.0f}sqft)")
    for note in result.notes:
        print(f"  note: {note}")


def test_full_pipeline(lat, lon, label):
    banner(f"measurement_v2_wireup — {label}")
    try:
        from roofmeasure.measurement_v2_wireup import measure_via_lidar_v2
    except ImportError as e:
        print(f"  IMPORT FAILED: {e}")
        return
    t = time.time()
    result = measure_via_lidar_v2(lat, lon)
    elapsed = time.time() - t
    if result is None or not result.success:
        print(f"  FAILED in {elapsed:.1f}s")
        if result:
            for n in result.notes:
                print(f"  note: {n}")
        return
    print(f"  OK in {elapsed:.1f}s")
    print(f"  source: {result.source}")
    print(f"  total area: {result.total_area_m2:.1f}m^2 "
          f"({result.total_area_m2 * 10.7639:.0f}sqft)")
    print(f"  predominant pitch: {result.predominant_pitch_x_in_12:.1f}/12")
    print(f"  facets: {len(result.facets)}")
    for n in result.notes:
        print(f"  note: {n}")


def main() -> None:
    addresses = [
        # Bedford TX house — we have EagleView ground truth for this
        ("Bedford TX", 32.829621, -97.152498),
        # Whitby ON — Canadian; expect lidar_v2_raw to return None
        ("Whitby ON", 43.907925, -78.971892),
    ]

    for label, lat, lon in addresses:
        fp = test_footprint(lat, lon, label)
        crop = test_lidar(fp, label)
        test_segmentation(crop, label)

    banner("FULL-PIPELINE TEST (measure_via_lidar_v2)")
    for label, lat, lon in addresses:
        test_full_pipeline(lat, lon, label)

    banner("SMOKE TEST COMPLETE")
    print(
        "Expected outcomes:\n"
        "  Bedford TX: footprint OK, lidar OK (raw points), segmentation 4-10 facets\n"
        "  Whitby ON: footprint OK (OSMnx or MS-CA), lidar None (no USGS in Canada)\n"
    )


if __name__ == "__main__":
    main()
