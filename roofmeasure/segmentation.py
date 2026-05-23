"""Roof plane segmentation from a cropped LiDAR point cloud.

Pure numpy, no scipy / open3d dependency. Pipeline:
  1. filter_to_roof_returns: drop ground / low-vegetation returns.
  2. extract_facets: iterative RANSAC plane fitting -> N facets with normal/d/inliers.
  3. facet_geometry: per-facet area (3D), pitch (rise/run), azimuth, planar outline.
  4. classify_edges: for each facet pair, find points near both planes -> shared edge,
     classify as ridge / hip / valley by comparing edge Z to per-facet Z distributions.
  5. Add boundary edges (eaves) per facet from outline perimeter minus shared-edge length.

All angles in degrees, lengths in meters, areas in m^2. Caller converts to ft/sqft.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RoofFacet:
    id: int
    plane_normal: Tuple[float, float, float]   # unit normal, z>0
    plane_d: float                              # plane: n.x = d
    inlier_indices: np.ndarray                  # indices into the source cloud
    area_m2: float
    pitch_deg: float                            # 0 = flat, 90 = vertical
    pitch_x_in_12: float                        # rise:12 (US convention)
    azimuth_deg: float                          # 0=N, 90=E, 180=S, 270=W
    centroid: Tuple[float, float, float]
    outline_xy: Optional[np.ndarray] = None     # (M, 2) convex hull in plane-local 2D


@dataclass
class RoofEdge:
    facet_a: int
    facet_b: Optional[int]                      # None for boundary (eave/rake)
    length_m: float
    midpoint: Tuple[float, float, float]
    kind: str                                   # ridge | hip | valley | eave | rake


@dataclass
class RoofSegmentation:
    facets: List[RoofFacet]
    edges: List[RoofEdge]
    ground_z: float
    point_count: int
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Step 1: ground filter
# ---------------------------------------------------------------------------

def filter_to_roof_returns(points: np.ndarray, min_hag_m: float = 1.5) -> Tuple[np.ndarray, float]:
    """Drop ground/low vegetation. Return (roof_candidate_points, ground_z)."""
    if len(points) == 0:
        return points, 0.0
    z = points[:, 2]
    ground_z = float(np.percentile(z, 1))
    mask = z > (ground_z + min_hag_m)
    return points[mask], ground_z


# ---------------------------------------------------------------------------
# Step 2: RANSAC plane fitting
# ---------------------------------------------------------------------------

def _fit_plane_lstsq(points: np.ndarray) -> Tuple[np.ndarray, float]:
    """Least-squares plane fit. Returns (unit_normal_with_z_positive, d)."""
    centroid = points.mean(axis=0)
    centered = points - centroid
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    normal = vh[-1]
    normal /= np.linalg.norm(normal)
    if normal[2] < 0:
        normal = -normal
    d = float(np.dot(normal, centroid))
    return normal, d


def _ransac_plane(points: np.ndarray, threshold_m: float = 0.15,
                  max_iter: int = 200, min_support: int = 60,
                  rng: Optional[np.random.Generator] = None
                  ) -> Optional[Tuple[np.ndarray, float, np.ndarray]]:
    rng = rng or np.random.default_rng()
    n = len(points)
    if n < min_support:
        return None
    best = None
    best_count = 0
    for _ in range(max_iter):
        idx = rng.choice(n, 3, replace=False)
        trio = points[idx]
        v1 = trio[1] - trio[0]
        v2 = trio[2] - trio[0]
        cross = np.cross(v1, v2)
        norm = np.linalg.norm(cross)
        if norm < 1e-6:
            continue
        normal = cross / norm
        if normal[2] < 0:
            normal = -normal
        d = float(np.dot(normal, trio[0]))
        dist = np.abs(points @ normal - d)
        inlier_mask = dist < threshold_m
        count = int(inlier_mask.sum())
        if count > best_count:
            best_count = count
            best = (normal, d, inlier_mask)
    if best is None or best_count < min_support:
        return None
    _, _, inlier_mask = best
    normal, d = _fit_plane_lstsq(points[inlier_mask])
    dist = np.abs(points @ normal - d)
    inlier_mask = dist < threshold_m
    return normal, d, inlier_mask


def extract_facets(points: np.ndarray, threshold_m: float = 0.15,
                   min_facet_points: int = 60, max_facets: int = 40,
                   seed: int = 17) -> List[Tuple[np.ndarray, float, np.ndarray]]:
    """Iterative RANSAC. Returns [(normal, d, mask_over_points), ...]."""
    rng = np.random.default_rng(seed)
    remaining_idx = np.arange(len(points))
    results = []
    for _ in range(max_facets):
        if len(remaining_idx) < min_facet_points:
            break
        sub = points[remaining_idx]
        fit = _ransac_plane(sub, threshold_m=threshold_m,
                            min_support=min_facet_points, rng=rng)
        if fit is None:
            break
        normal, d, inlier_mask = fit
        full_mask = np.zeros(len(points), dtype=bool)
        full_mask[remaining_idx[inlier_mask]] = True
        results.append((normal, d, full_mask))
        remaining_idx = remaining_idx[~inlier_mask]
    return results


# ---------------------------------------------------------------------------
# Step 3: facet geometry
# ---------------------------------------------------------------------------

def _convex_hull_2d(points_xy: np.ndarray) -> np.ndarray:
    """Andrew's monotone chain. Returns hull (M, 2)."""
    pts = sorted(map(tuple, points_xy))
    if len(pts) <= 1:
        return np.array(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


def _polygon_area_2d(poly: np.ndarray) -> float:
    if len(poly) < 3:
        return 0.0
    x = poly[:, 0]; y = poly[:, 1]
    return 0.5 * float(np.abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def facet_geometry(points: np.ndarray, normal: np.ndarray, d: float,
                   facet_id: int, inlier_mask: np.ndarray) -> RoofFacet:
    inliers = points[inlier_mask]
    centroid = inliers.mean(axis=0)
    pitch_deg = math.degrees(math.acos(min(1.0, max(0.0, float(normal[2])))))
    if normal[2] > 0.9999:
        pitch_x_in_12 = 0.0
    else:
        pitch_x_in_12 = 12.0 * math.sqrt(normal[0] ** 2 + normal[1] ** 2) / normal[2]
    if abs(normal[0]) < 1e-6 and abs(normal[1]) < 1e-6:
        azimuth_deg = 0.0
    else:
        az = math.degrees(math.atan2(-normal[0], -normal[1]))
        azimuth_deg = (az + 360.0) % 360.0
    # Build a 2D basis on the plane to project inliers and measure planar area
    n = normal
    if abs(n[2]) < 0.9:
        u = np.cross(n, np.array([0.0, 0.0, 1.0]))
    else:
        u = np.cross(n, np.array([1.0, 0.0, 0.0]))
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    rel = inliers - centroid
    proj = np.column_stack([rel @ u, rel @ v])
    hull = _convex_hull_2d(proj)
    planar_area = _polygon_area_2d(hull)
    return RoofFacet(
        id=facet_id,
        plane_normal=(float(n[0]), float(n[1]), float(n[2])),
        plane_d=float(d),
        inlier_indices=np.flatnonzero(inlier_mask),
        area_m2=planar_area,
        pitch_deg=pitch_deg,
        pitch_x_in_12=pitch_x_in_12,
        azimuth_deg=azimuth_deg,
        centroid=(float(centroid[0]), float(centroid[1]), float(centroid[2])),
        outline_xy=hull,
    )


# ---------------------------------------------------------------------------
# Step 4: edge classification
# ---------------------------------------------------------------------------

def _intersect_planes(n1: np.ndarray, d1: float, n2: np.ndarray, d2: float
                      ) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    direction = np.cross(n1, n2)
    norm = np.linalg.norm(direction)
    if norm < 1e-6:
        return None
    direction /= norm
    A = np.array([n1, n2])
    rhs = np.array([d1, d2])
    abs_dir = np.abs(direction)
    fix = int(np.argmax(abs_dir))
    cols = [i for i in range(3) if i != fix]
    Asub = A[:, cols]
    try:
        sol = np.linalg.solve(Asub, rhs)
    except np.linalg.LinAlgError:
        return None
    point = np.zeros(3)
    point[cols[0]] = sol[0]
    point[cols[1]] = sol[1]
    return point, direction


def _classify_edge(facet_a_pts: np.ndarray, facet_b_pts: np.ndarray,
                   shared_pts: np.ndarray, dir_vec: np.ndarray) -> str:
    """Robust classifier using actual facet inlier points.

      - Edge at TOP of both -> hip (sloped) or ridge (horizontal).
      - Edge at BOTTOM of both -> valley.
      - Otherwise -> hip (fallback).
    """
    edge_z = float(np.median(shared_pts[:, 2]))
    a_lo, a_hi = float(np.percentile(facet_a_pts[:, 2], 10)), float(np.percentile(facet_a_pts[:, 2], 90))
    b_lo, b_hi = float(np.percentile(facet_b_pts[:, 2], 10)), float(np.percentile(facet_b_pts[:, 2], 90))

    def near_top(z, lo, hi, tol=0.30):
        return z >= hi - tol * max(0.5, hi - lo)

    def near_bottom(z, lo, hi, tol=0.30):
        return z <= lo + tol * max(0.5, hi - lo)

    a_top = near_top(edge_z, a_lo, a_hi)
    b_top = near_top(edge_z, b_lo, b_hi)
    a_bot = near_bottom(edge_z, a_lo, a_hi)
    b_bot = near_bottom(edge_z, b_lo, b_hi)
    horizontal = abs(dir_vec[2]) < 0.08

    if a_top and b_top:
        return "ridge" if horizontal else "hip"
    if a_bot and b_bot:
        return "valley"
    return "hip"


def classify_edges(facets: List[RoofFacet], points: np.ndarray,
                   threshold_m: float = 0.35, min_edge_points: int = 20
                   ) -> List[RoofEdge]:
    edges: List[RoofEdge] = []
    n_facets = len(facets)
    for i in range(n_facets):
        for j in range(i + 1, n_facets):
            fa = facets[i]; fb = facets[j]
            na = np.array(fa.plane_normal); nb = np.array(fb.plane_normal)
            dist_a = np.abs(points @ na - fa.plane_d)
            dist_b = np.abs(points @ nb - fb.plane_d)
            mask = (dist_a < threshold_m) & (dist_b < threshold_m)
            if mask.sum() < min_edge_points:
                continue
            shared = points[mask]
            inter = _intersect_planes(na, fa.plane_d, nb, fb.plane_d)
            if inter is None:
                continue
            p0, dir_vec = inter
            t = (shared - p0) @ dir_vec
            t_min, t_max = float(t.min()), float(t.max())
            length = t_max - t_min
            if length < 0.5:
                continue
            mid = p0 + dir_vec * ((t_min + t_max) / 2)
            facet_a_pts = points[fa.inlier_indices]
            facet_b_pts = points[fb.inlier_indices]
            kind = _classify_edge(facet_a_pts, facet_b_pts, shared, dir_vec)
            edges.append(RoofEdge(
                facet_a=fa.id, facet_b=fb.id,
                length_m=length,
                midpoint=(float(mid[0]), float(mid[1]), float(mid[2])),
                kind=kind,
            ))
    # Boundary edges (eaves) per-facet: outline perimeter minus shared edges
    for fa in facets:
        if fa.outline_xy is None or len(fa.outline_xy) < 3:
            continue
        perim = 0.0
        outline = fa.outline_xy
        for k in range(len(outline)):
            a = outline[k]; b = outline[(k + 1) % len(outline)]
            perim += float(np.linalg.norm(b - a))
        shared_len = sum(e.length_m for e in edges if fa.id in (e.facet_a, e.facet_b))
        boundary_len = max(0.0, perim - shared_len)
        if boundary_len > 0.5:
            kind = "eave" if fa.pitch_x_in_12 > 0.5 else "boundary"
            edges.append(RoofEdge(
                facet_a=fa.id, facet_b=None,
                length_m=boundary_len,
                midpoint=fa.centroid,
                kind=kind,
            ))
    return edges


# ---------------------------------------------------------------------------
# Top-level pipeline
# ---------------------------------------------------------------------------

def segment_roof(points: np.ndarray, plane_threshold_m: float = 0.15,
                 min_facet_points: int = 60, max_facets: int = 40
                 ) -> RoofSegmentation:
    roof_pts, ground_z = filter_to_roof_returns(points)
    notes: List[str] = []
    if len(roof_pts) < min_facet_points:
        notes.append(
            f"Only {len(roof_pts)} roof returns above ground+1.5m - results unreliable.")
    fits = extract_facets(roof_pts, threshold_m=plane_threshold_m,
                          min_facet_points=min_facet_points, max_facets=max_facets)
    facets = [facet_geometry(roof_pts, normal, d, idx, mask)
              for idx, (normal, d, mask) in enumerate(fits)]
    edges = classify_edges(facets, roof_pts)
    if not facets:
        notes.append("RANSAC found no planar facets; input cloud may be too sparse/noisy.")
    return RoofSegmentation(
        facets=facets, edges=edges, ground_z=ground_z,
        point_count=len(points), notes=notes,
    )
