"""Standalone smoke test for the v2 pipeline.

Run after deploying the v2 modules:
    sudo /home/roofmeasure/engine/venv/bin/python /home/roofmeasure/engine/tests/test_v2_pipeline.py

Tests each new module independently so we can see exactly where it breaks if
something is wrong. Tries a known US residential address (Bedford TX) and
a Canadian address (74 Medland Ave, Whitby).

Expected: US works fully; CA falls through to None on lidar_v2 (no USGS
coverage in Canada — that's correct behavior).
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

# Add engine root to path so we can import roofmeasure.*
sys.path.insert(0, "/home/roofmeasure/engine")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("smoke")


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def test_footprint(lat: float, lon: float, label: str) -> object:
    banner(f"footprint_v2 — {label} ({lat}, {lon})")
    try:
        from roofmeasure.footprint_v2 import get_building_footprint
    except ImportError as e:
        print(f"  IMPORT FAILED: {e}")
        return None
    start = time.time()
    fp = get_building_footprint(lat, lon)
    elapsed = time.time() - start
    if fp is None:
        print(f"  NO FOOTPRINT (after {elapsed:.1f}s)")
        return None
    print(f"  OK in {elapsed:.1f}s")
    print(f"  source: {fp.source}")
    print(f"  vertices: {len(fp.polygon_lonlat)}")
    print(f"  centroid: {fp.centroid_lonlat}")
    return fp


def test_lidar(fp: object, label: str) -> object:
    banner(f"lidar_v2 — {label}")
    if fp is None:
        print("  skipped (no footprint)")
        return None
    try:
        from roofmeasure.lidar_v2 import fetch_lidar_for_footprint
    except ImportError as e:
        print(f"  IMPORT FAILED: {e}")
        return None
    start = time.time()
    crop = fetch_lidar_for_footprint(fp)
    elapsed = time.time() - start
    if crop is None:
        print(f"  NO LIDAR (after {elapsed:.1f}s) — expected for Canada / sparse areas")
        return None
    print(f"  OK in {elapsed:.1f}s")
    print(f"  points: {len(crop.points_local_m)}")
    print(f"  source: {crop.source}")
    print(f"  origin: {crop.crs_origin_lonlat}")
    return crop


def test_segmentation(crop: object, label: str) -> None:
    banner(f"segmentation_v2 — {label}")
    if crop is None:
        print("  skipped (no LIDAR points)")
        return
    try:
        from roofmeasure.segmentation_v2 import segment_roof
    except ImportError as e:
        print(f"  IMPORT FAILED: {e}")
        return
    start = time.time()
    result = segment_roof(crop.points_local_m)
    elapsed = time.time() - start
    print(f"  OK in {elapsed:.1f}s")
    print(f"  facets: {len(result.facets)}")
    print(f"  ground_z: {result.ground_z:.2f}m")
    for f in result.facets[:6]:
        print(
            f"    facet #{f.id}: area={f.area_m2:.1f}m^2, "
            f"pitch={f.pitch_deg:.1f}deg ({f.pitch_x_in_12:.1f}/12), "
            f"azimuth={f.azimuth_deg:.0f}deg"
        )
    for note in result.notes:
        print(f"  note: {note}")


def main() -> None:
    addresses = [
        # Bedford TX house — we have EagleView ground truth for this
        ("Bedford TX", 32.829621, -97.152498),
        # Whitby ON — Canadian; expect lidar_v2 to return None (no USGS coverage)
        ("Whitby ON", 43.907925, -78.971892),
    ]

    for label, lat, lon in addresses:
        fp = test_footprint(lat, lon, label)
        crop = test_lidar(fp, label)
        test_segmentation(crop, label)

    banner("SMOKE TEST COMPLETE")
    print(
        "Expected outcomes:\n"
        "  Bedford TX: footprint OK, lidar OK, segmentation extracts 2-8 facets\n"
        "  Whitby ON: footprint OK (via OSMnx with proper User-Agent), lidar None (no USGS in Canada)\n"
    )


if __name__ == "__main__":
    main()
