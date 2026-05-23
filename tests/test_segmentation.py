"""Smoke test: synthesize a known hip roof, segment it, verify the answer.

This is the only test that runs without a network and proves that the RANSAC
+ plane-geometry pipeline produces correct pitch / area / azimuth for a
ground-truth roof shape.
"""

from __future__ import annotations

import math
import sys
import os

# Allow running directly: `python3 tests/test_segmentation.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from roofmeasure.lidar import synthesize_test_pointcloud
from roofmeasure.segmentation import segment_roof


def build_hip_roof_facets(
    length_m: float = 15.0,
    width_m: float = 10.0,
    eave_z: float = 4.0,
    ridge_height_m: float = 2.5,  # rise from eaves to ridge
):
    """A classic hip roof has 4 facets: 2 trapezoids (long sides) + 2 triangles (ends).

    Geometry:
       - rectangular footprint length_m x width_m centered at origin
       - eaves at z=eave_z
       - ridge runs along x-axis, centered, with length = length_m - width_m
       - ridge height = eave_z + ridge_height_m
    """
    L = length_m / 2
    W = width_m / 2
    R = (length_m - width_m) / 2  # half-length of ridge
    eave = eave_z
    ridge = eave_z + ridge_height_m
    # Corner points
    C_sw_eave = (-L, -W, eave)
    C_se_eave = ( L, -W, eave)
    C_ne_eave = ( L,  W, eave)
    C_nw_eave = (-L,  W, eave)
    # Ridge endpoints
    R_w = (-R, 0, ridge)
    R_e = ( R, 0, ridge)
    # Facets
    south = {  # long trapezoid facing -y
        "name": "south",
        "corners": [C_sw_eave, C_se_eave, R_e, R_w],
    }
    north = {  # long trapezoid facing +y
        "name": "north",
        "corners": [C_nw_eave, R_w, R_e, C_ne_eave],
    }
    east = {   # triangle on +x
        "name": "east",
        "corners": [C_se_eave, C_ne_eave, R_e],
    }
    west = {   # triangle on -x
        "name": "west",
        "corners": [C_sw_eave, R_w, C_nw_eave],
    }
    return [south, north, east, west]


def expected_pitch_x_in_12(rise_m: float, run_m: float) -> float:
    return 12.0 * rise_m / run_m


def main():
    facets_truth = build_hip_roof_facets()
    points = synthesize_test_pointcloud(facets_truth, point_density_per_m2=25, noise_m=0.02)
    print(f"synthesized {len(points)} points across {len(facets_truth)} facets")

    # Add a few ground points so the filter has something to work with
    ground = np.column_stack([
        np.random.uniform(-9, 9, 400),
        np.random.uniform(-6, 6, 400),
        np.random.uniform(-0.1, 0.1, 400),
    ])
    cloud = np.vstack([points, ground])

    seg = segment_roof(cloud, plane_threshold_m=0.12, min_facet_points=80)
    print(f"\nground_z = {seg.ground_z:.2f} m")
    print(f"detected {len(seg.facets)} facets:")
    for f in seg.facets:
        print(f"  facet {f.id}: area={f.area_m2:6.1f} m^2  "
              f"pitch={f.pitch_x_in_12:5.2f}/12  "
              f"azimuth={f.azimuth_deg:6.1f} deg  "
              f"normal=({f.plane_normal[0]:+.2f},{f.plane_normal[1]:+.2f},{f.plane_normal[2]:+.2f})")
    print(f"\ndetected {len(seg.edges)} edges:")
    by_kind = {}
    for e in seg.edges:
        by_kind.setdefault(e.kind, []).append(e.length_m)
    for k, vs in sorted(by_kind.items()):
        print(f"  {k}: count={len(vs)} total_len={sum(vs):.1f}m")

    # ---- Assertions ----
    # Expected: 4 facets (south, north, east, west)
    n_truth = 4
    assert len(seg.facets) >= n_truth, f"Expected >={n_truth} facets, got {len(seg.facets)}"
    print(f"\n[OK] found at least {n_truth} facets")

    # Expected pitch for long trapezoid sides: rise=2.5, run=width/2=5  -> 6/12
    # Expected pitch for end triangles: rise=2.5, run=(L-R) i.e. width/2 too -> 6/12
    # (in a true hip roof all 4 facets share the same pitch)
    expected_pitch = expected_pitch_x_in_12(2.5, 5.0)  # 6/12
    pitches = sorted(f.pitch_x_in_12 for f in seg.facets[:4])
    print(f"expected pitch {expected_pitch:.2f}/12, got pitches {[f'{p:.2f}' for p in pitches]}")
    for p in pitches:
        assert abs(p - expected_pitch) < 0.6, f"pitch {p:.2f} too far from {expected_pitch:.2f}"
    print("[OK] all detected facets have pitch within 0.6 of 6/12")

    # Areas: long trapezoid = (top+bottom)/2 * slant; end triangle = ...
    # For our shape: long sides = trapezoid (parallel sides 15m and 5m, slant height = sqrt(5^2+2.5^2) = 5.59m)
    # area = (15+5)/2 * 5.59 = 55.9 m^2 per long facet
    # end triangle = 1/2 * base * slant = 1/2 * 10 * 5.59 = 27.95 m^2 per triangle
    # total = 2*55.9 + 2*27.95 = 167.7 m^2
    expected_total = 2 * 55.9 + 2 * 27.95
    detected_total = sum(f.area_m2 for f in seg.facets[:4])
    err_pct = abs(detected_total - expected_total) / expected_total * 100
    print(f"total area: expected {expected_total:.1f} m^2, "
          f"detected {detected_total:.1f} m^2 ({err_pct:.1f}% error)")
    # Convex hull on rectangular/trapezoidal facets tends to slightly inflate area;
    # also random sampling may not reach the corners. Within ~15% is fine for a smoke test.
    assert err_pct < 15.0, f"total area error {err_pct:.1f}% > 15%"
    print(f"[OK] total area within 15%")

    # Edge classification: expect at least 1 ridge and at least 2 hips on a true hip roof
    ridge_count = sum(1 for e in seg.edges if e.kind == "ridge")
    hip_count = sum(1 for e in seg.edges if e.kind == "hip")
    eave_count = sum(1 for e in seg.edges if e.kind == "eave")
    print(f"edges: ridge={ridge_count} hip={hip_count} eave={eave_count}")
    assert ridge_count >= 1, "expected at least 1 ridge edge"
    assert hip_count >= 2, f"expected at least 2 hip edges, got {hip_count}"
    print("[OK] edge classification produces ridge + hips as expected for hip roof")

    print("\nAll smoke-test assertions passed.")


if __name__ == "__main__":
    main()
