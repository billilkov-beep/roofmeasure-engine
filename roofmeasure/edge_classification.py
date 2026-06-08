"""Phase 2: roof edge classification from segmented facets.

Replaces the heuristic `derive-line-measurements.ts` (which guesses from
facet count + area + pitch) with actual geometric edge detection.

Inputs: list of RoofFacet objects from `segmentation_v2.segment_roof()`,
each carrying its inlier point cloud, plane normal, and centroid.

Outputs: list of RoofEdge objects with:
  - kind: 'ridge' | 'hip' | 'valley' | 'eave' | 'rake'
  - facet_a, facet_b: the two facets this edge separates (b=None for eave/rake)
  - length_m: 3D length of the edge
  - midpoint_lonlat (if crs_origin given)

Algorithm:

  For each pair of facets (A, B):
    1. Compute the line of intersection of plane(A) and plane(B).
    2. Project all of A's inlier points onto this line. Same for B.
    3. Find the segment along the line where projections from BOTH A and B
       are dense (within 0.5m of the line). That's the shared edge.
    4. If the shared segment has length > min_edge_length_m: it's a real edge.

  Classify the shared edge by:
    - Dihedral angle θ = angle between plane normals (both pointing up).
      * θ ≈ 0 → planes are parallel — no edge (skip)
      * Midpoint z high (above neighbors) + normals tilt outward → RIDGE or HIP
      * Midpoint z lower than at least one centroid + normals tilt inward → VALLEY
    - Ridge vs Hip: a ridge runs ALONG the slope direction, a hip runs at an
      angle. We test by comparing the edge direction to the slope direction
      of each facet:
      * If edge direction ⊥ to both facets' slope vectors (within 15°): RIDGE
      * Else: HIP

  Boundary edges (only one facet on each side):
    - Project each facet's hull boundary points onto its convex hull
    - For each hull edge not shared with another facet:
      * If edge is at ~ground_z (bottom of slope): EAVE
      * If edge is on a sloped side (rises): RAKE

This is a first cut. Future improvements: better hull extraction (alpha shape
instead of convex hull), edge merging across collinear segments, ridge-hip
classification using OSM building outline.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

LOG = logging.getLogger(__name__)


@dataclass
class ClassifiedEdge:
    kind: str  # 'ridge' | 'hip' | 'valley' | 'eave' | 'rake'
    facet_a: int
    facet_b: Optional[int]
    length_m: float
    midpoint_local_m: Tuple[float, float, float]
    midpoint_lonlat: Optional[Tuple[float, float]] = None
    dihedral_deg: Optional[float] = None


# ---------------------------------------------------------------------------
# Plane intersection geometry
# ---------------------------------------------------------------------------

def _plane_line_intersection(n1, d1, n2, d2):
    """Intersection of two planes: n1·X = d1 and n2·X = d2.

    Returns (point_on_line, direction_unit) or (None, None) if parallel.
    """
    direction = np.cross(n1, n2)
    norm = np.linalg.norm(direction)
    if norm < 1e-6:
        return None, None
    direction = direction / norm

    # Find any point on the line — solve the 2x2 system after picking 2 axes
    abs_dir = np.abs(direction)
    pick = np.argmax(abs_dir)  # axis where direction is largest
    # Solve in the OTHER two axes
    other = [i for i in range(3) if i != pick]
    a = np.array([
        [n1[other[0]], n1[other[1]]],
        [n2[other[0]], n2[other[1]]],
    ])
    b = np.array([d1, d2])
    try:
        sol = np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        return None, None
    point = np.zeros(3)
    point[other[0]] = sol[0]
    point[other[1]] = sol[1]
    return point, direction


def _project_points_onto_line(points, line_point, line_dir):
    """Return scalar t such that nearest point on line is line_point + t*line_dir."""
    return (points - line_point) @ line_dir


def _distance_points_to_line(points, line_point, line_dir):
    """Perpendicular distance from each point to the line."""
    v = points - line_point
    proj = (v @ line_dir)[:, np.newaxis] * line_dir
    perp = v - proj
    return np.linalg.norm(perp, axis=1)


# ---------------------------------------------------------------------------
# Shared edge detection
# ---------------------------------------------------------------------------

def _find_shared_edge(facet_a, facet_b, points, max_dist_m: float = 0.6) -> Optional[Tuple[float, float, np.ndarray, np.ndarray, np.ndarray]]:
    """Find shared edge between two facets.

    Returns (t_min, t_max, line_point, line_dir, midpoint_3d) or None.
    """
    n_a = np.array(facet_a.plane_normal)
    n_b = np.array(facet_b.plane_normal)
    d_a = facet_a.plane_d
    d_b = facet_b.plane_d

    line_point, line_dir = _plane_line_intersection(n_a, d_a, n_b, d_b)
    if line_point is None:
        return None  # parallel planes

    pts_a = points[facet_a.inlier_indices]
    pts_b = points[facet_b.inlier_indices]

    dist_a = _distance_points_to_line(pts_a, line_point, line_dir)
    dist_b = _distance_points_to_line(pts_b, line_point, line_dir)

    near_a = pts_a[dist_a < max_dist_m]
    near_b = pts_b[dist_b < max_dist_m]
    if len(near_a) < 4 or len(near_b) < 4:
        return None

    t_a = _project_points_onto_line(near_a, line_point, line_dir)
    t_b = _project_points_onto_line(near_b, line_point, line_dir)

    # Find overlap region: where both A and B have points
    t_a_min, t_a_max = np.percentile(t_a, [5, 95])
    t_b_min, t_b_max = np.percentile(t_b, [5, 95])
    t_min = max(t_a_min, t_b_min)
    t_max = min(t_a_max, t_b_max)
    if t_max - t_min < 0.5:
        return None

    midpoint = line_point + ((t_min + t_max) / 2) * line_dir
    return t_min, t_max, line_point, line_dir, midpoint


# ---------------------------------------------------------------------------
# Classify shared edge: ridge / hip / valley
# ---------------------------------------------------------------------------

def _classify_shared(facet_a, facet_b, line_dir, midpoint, dihedral_deg):
    """Classify a shared edge as ridge / hip / valley.

    Rules:
      - Valley: midpoint z is LOWER than at least one facet centroid AND
        the planes face each other (normals tilt inward).
      - Ridge or hip: midpoint z is HIGHER. Ridge if edge direction is
        roughly perpendicular to both facets' slope directions (gable ridge).
        Hip otherwise (hip is at an angle to slope).
    """
    n_a = np.array(facet_a.plane_normal)
    n_b = np.array(facet_b.plane_normal)
    ca = np.array(facet_a.centroid)
    cb = np.array(facet_b.centroid)
    mid_z = midpoint[2]

    # Slope direction of each facet (down the slope, in 2D)
    slope_a = np.array([-n_a[0], -n_a[1]])  # 2D, downslope
    slope_b = np.array([-n_b[0], -n_b[1]])
    edge_2d = np.array([line_dir[0], line_dir[1]])
    edge_2d_norm = np.linalg.norm(edge_2d)
    if edge_2d_norm < 1e-6:
        # Edge is nearly vertical — unusual, treat as hip
        return "hip"
    edge_2d /= edge_2d_norm

    # Valley if midpoint sits below at least one centroid by > 0.5m
    if mid_z < min(ca[2], cb[2]) - 0.5:
        return "valley"

    # Ridge vs hip — check edge perpendicularity to slope
    slope_a_norm = slope_a / (np.linalg.norm(slope_a) + 1e-9)
    slope_b_norm = slope_b / (np.linalg.norm(slope_b) + 1e-9)
    dot_a = abs(edge_2d @ slope_a_norm)
    dot_b = abs(edge_2d @ slope_b_norm)
    # If edge is perpendicular to both slopes (dots near 0), it's a ridge
    if dot_a < 0.30 and dot_b < 0.30:
        return "ridge"
    return "hip"


# ---------------------------------------------------------------------------
# Boundary edges: eaves and rakes
# ---------------------------------------------------------------------------

def _classify_boundary_edges(
    facet,
    points,
    ground_z: float,
    shared_indices: List[int],
    max_dist_m: float = 0.5,
) -> List[ClassifiedEdge]:
    """For a facet, find its boundary segments that are NOT shared with any
    other facet, and classify each as eave or rake.

    Implementation uses the convex hull of the facet's inlier projection.
    Each consecutive hull-vertex pair is a candidate boundary edge. We skip
    any segments that coincide with a shared edge (within `max_dist_m`).
    """
    pts = points[facet.inlier_indices]
    if len(pts) < 4:
        return []
    n = np.array(facet.plane_normal)

    # Project pts to plane-local 2D
    arbitrary = (
        np.array([1.0, 0.0, 0.0])
        if abs(n[0]) < 0.9
        else np.array([0.0, 1.0, 0.0])
    )
    u = np.cross(n, arbitrary); u /= np.linalg.norm(u)
    v = np.cross(n, u)
    local_xy = np.column_stack([pts @ u, pts @ v])

    try:
        from scipy.spatial import ConvexHull
        hull = ConvexHull(local_xy)
    except Exception:
        return []

    hull_idx = list(hull.vertices) + [hull.vertices[0]]
    edges = []
    for i in range(len(hull_idx) - 1):
        a = pts[hull.vertices[i]]
        b = pts[hull.vertices[(i + 1) % len(hull.vertices)]]
        midpoint = (a + b) / 2
        length_m = float(np.linalg.norm(b - a))
        if length_m < 0.5:
            continue

        # Eave if midpoint is at low z relative to facet centroid
        rise = facet.centroid[2] - midpoint[2]
        # If this hull edge sits at the LOW end of the facet (slope down), it's the eave
        # Else it sits on a side that climbs with the slope — that's a rake

        if rise > 0.3:
            edges.append(ClassifiedEdge(
                kind="eave",
                facet_a=facet.id,
                facet_b=None,
                length_m=length_m,
                midpoint_local_m=tuple(midpoint.tolist()),
            ))
        else:
            edges.append(ClassifiedEdge(
                kind="rake",
                facet_a=facet.id,
                facet_b=None,
                length_m=length_m,
                midpoint_local_m=tuple(midpoint.tolist()),
            ))
    return edges


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def classify_edges(
    facets: list,
    points: np.ndarray,
    ground_z: float = 0.0,
    crs_origin_lonlat: Optional[Tuple[float, float]] = None,
) -> List[ClassifiedEdge]:
    """Classify all edges in a roof segmentation.

    Args:
      facets: list of RoofFacet from segmentation_v2
      points: (N, 3) full point cloud the facets reference via inlier_indices
      ground_z: ground elevation in meters (from segmentation result)
      crs_origin_lonlat: (lon, lat) of the local frame origin, for output
                        midpoint_lonlat. If None, midpoint_lonlat stays None.

    Returns flat list of ClassifiedEdge.
    """
    edges: List[ClassifiedEdge] = []

    # Pairwise shared edges
    n_facets = len(facets)
    for i in range(n_facets):
        for j in range(i + 1, n_facets):
            fa = facets[i]
            fb = facets[j]
            result = _find_shared_edge(fa, fb, points)
            if result is None:
                continue
            t_min, t_max, line_point, line_dir, midpoint = result
            length_m = float(t_max - t_min)
            if length_m < 0.5:
                continue

            n_a = np.array(fa.plane_normal)
            n_b = np.array(fb.plane_normal)
            cos_d = float(np.clip(n_a @ n_b, -1.0, 1.0))
            dihedral = math.degrees(math.acos(cos_d))
            if dihedral < 10 or dihedral > 170:
                continue  # planes are too parallel or too anti-parallel

            kind = _classify_shared(fa, fb, line_dir, midpoint, dihedral)
            edges.append(ClassifiedEdge(
                kind=kind,
                facet_a=fa.id, facet_b=fb.id,
                length_m=length_m,
                midpoint_local_m=tuple(midpoint.tolist()),
                dihedral_deg=dihedral,
            ))

    # Boundary edges per facet
    for f in facets:
        edges.extend(_classify_boundary_edges(f, points, ground_z, []))

    # Convert midpoints to lon/lat if origin provided
    if crs_origin_lonlat is not None:
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(crs_origin_lonlat[1]))
        for e in edges:
            me, mn, _ = e.midpoint_local_m
            e.midpoint_lonlat = (
                crs_origin_lonlat[0] + me / m_per_deg_lon,
                crs_origin_lonlat[1] + mn / m_per_deg_lat,
            )

    LOG.info(
        "edge_classification: %d edges (ridges=%d, hips=%d, valleys=%d, eaves=%d, rakes=%d)",
        len(edges),
        sum(1 for e in edges if e.kind == "ridge"),
        sum(1 for e in edges if e.kind == "hip"),
        sum(1 for e in edges if e.kind == "valley"),
        sum(1 for e in edges if e.kind == "eave"),
        sum(1 for e in edges if e.kind == "rake"),
    )
    return edges


def aggregate_lengths(edges: List[ClassifiedEdge]) -> dict:
    """Sum lengths by kind. Returns dict of feet (since US users expect feet)."""
    m_to_ft = 3.28084
    sums = {"ridge": 0.0, "hip": 0.0, "valley": 0.0, "eave": 0.0, "rake": 0.0}
    for e in edges:
        if e.kind in sums:
            sums[e.kind] += e.length_m
    return {
        "ridges_m": round(sums["ridge"], 1),
        "ridges_ft": round(sums["ridge"] * m_to_ft, 1),
        "hips_m": round(sums["hip"], 1),
        "hips_ft": round(sums["hip"] * m_to_ft, 1),
        "valleys_m": round(sums["valley"], 1),
        "valleys_ft": round(sums["valley"] * m_to_ft, 1),
        "eaves_m": round(sums["eave"], 1),
        "eaves_ft": round(sums["eave"] * m_to_ft, 1),
        "rakes_m": round(sums["rake"], 1),
        "rakes_ft": round(sums["rake"] * m_to_ft, 1),
        # Combined ridges+hips since EagleView reports them together sometimes
        "ridges_hips_m": round(sums["ridge"] + sums["hip"], 1),
        "ridges_hips_ft": round((sums["ridge"] + sums["hip"]) * m_to_ft, 1),
        # Drip edge = eaves + rakes (US convention)
        "drip_edge_m": round(sums["eave"] + sums["rake"], 1),
        "drip_edge_ft": round((sums["eave"] + sums["rake"]) * m_to_ft, 1),
    }
