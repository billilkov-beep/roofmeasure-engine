"""Offline demo: shows the full output shape without needing network access.

Run:  python examples/demo_offline.py

This uses a fake (in-memory) building footprint + a synthetic LiDAR cloud
that matches the footprint. It bypasses the geocoder and the LiDAR fetch so
you can see the engine's output without setting up API keys or installing
laspy. For real measurements, use `python measure.py "<address>"`.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from roofmeasure.footprint import BuildingFootprint
from roofmeasure.geocode import GeocodeResult
from roofmeasure.lidar import LidarCrop
from roofmeasure.measurement import _build_measurement, _synthetic_cloud_for_footprint
from roofmeasure.obstructions import detect_obstructions_from_residuals
from roofmeasure.segmentation import segment_roof


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Pretend we geocoded an address in Toronto
    address = "123 Sample St, Toronto, ON"
    gc = GeocodeResult(
        lat=43.6511, lon=-79.3849, matched_address=address,
        source="(mock_offline)", country="CA",
    )

    # Build a small rectangular footprint, ~16m x 22m, area ~150 m^2
    poly = [
        (-79.385,  43.651),
        (-79.3848, 43.651),
        (-79.3848, 43.6512),
        (-79.385,  43.6512),
        (-79.385,  43.651),
    ]
    fp = BuildingFootprint(
        polygon_lonlat=poly,
        source="(mock_offline)",
        footprint_area_m2=150.0,
        centroid_lonlat=(gc.lon, gc.lat),
    )

    # Synthesize a hip roof matched to the footprint
    cloud = _synthetic_cloud_for_footprint(fp, gc.lon, gc.lat)
    crop = LidarCrop(
        points_local_m=cloud,
        crs_origin_lonlat=(gc.lon, gc.lat),
        source="(synthetic_hip_roof)",
    )
    print(f"synthetic cloud: {len(cloud)} points "
          f"(z range {cloud[:,2].min():.1f}m to {cloud[:,2].max():.1f}m)")

    # Run the real segmentation + obstruction modules
    seg = segment_roof(cloud)
    obs = detect_obstructions_from_residuals(cloud, seg.facets)

    # Roll up to the EagleView-style output
    result = _build_measurement(
        gc, fp, fp_area_m2=150.0, crop=crop, seg=seg, obs=obs,
        price_low=450, price_high=850, min_cents=250000, waste_pct=12.0,
    )

    print("\n" + "=" * 70)
    print(f"  ROOF MEASUREMENT for {address}")
    print("=" * 70)
    print(f"  Footprint area       : {result.footprintSqFt} sq ft")
    print(f"  Total roof area      : {result.roofAreaSqFt} sq ft "
          f"(slope factor {result.roofAreaSqFt / result.footprintSqFt:.3f}x)")
    print(f"  Roofing squares      : {result.roofingSquares}")
    print(f"  Predominant pitch    : {result.predominantPitch}")
    print(f"  Facet count          : {result.facetCount}")
    print(f"  Confidence score     : {result.confidenceScore}/100")
    print(f"  Estimated cost       : ${int(result.estimatedCostLow):,} - "
          f"${int(result.estimatedCostHigh):,}")

    print("\n  Per-facet breakdown:")
    print(f"    {'#':<3} {'area_sqft':>10}  {'pitch':>6}  {'azimuth':>8}  facing")
    for f in result.facets:
        az = f["azimuthDeg"]
        cardinal = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((az + 22.5) % 360 / 45)]
        print(f"    {f['id']:<3} {f['areaSqFt']:>10}  {f['pitch']:>6}  "
              f"{az:>7.1f}d  {cardinal}")

    print("\n  Line measurements (linear feet):")
    lm = result.lineMeasurements
    print(f"    Ridges          : {lm.get('ridgesFt', 0):>8} ft")
    print(f"    Hips            : {lm.get('hipsFt', 0):>8} ft")
    print(f"    Ridges + Hips   : {lm.get('ridgesHipsFt', 0):>8} ft")
    print(f"    Valleys         : {lm.get('valleysFt', 0):>8} ft")
    print(f"    Eaves           : {lm.get('eavesFt', 0):>8} ft")
    print(f"    Rakes           : {lm.get('rakesFt', 0):>8} ft")
    print(f"    Drip edge total : {lm.get('dripEdgeFt', 0):>8} ft")
    print(f"    Penetrations    : {int(lm.get('penetrations', 0)):>8}")

    if result.pitchAreas:
        print("\n  Pitch breakdown:")
        for p in result.pitchAreas:
            print(f"    {p['pitch']:>6}  -> {p['areaSqFt']:>8} sq ft  ({p['percent']:.1f}%)")

    print("\n  Data sources:")
    for k, v in result.dataSources.items():
        if v is not None:
            print(f"    {k:<14}: {v}")

    out_path = ROOT / "examples" / "demo_offline_output.json"
    out_path.write_text(result.to_json())
    print(f"\n  Full JSON written to: {out_path}")


if __name__ == "__main__":
    main()
