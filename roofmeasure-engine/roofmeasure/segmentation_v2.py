"""Roof plane segmentation v3.3 — point-assignment area calc.

v3.3 fix: after RANSAC+merge gives N candidate planes, REASSIGN every roof
point to its closest plane (3D perpendicular distance). Per-facet area is
then the convex hull (XY) of that facet's UNIQUELY assigned points,
slope-corrected. No overlap by construction — each point belongs to exactly
one facet — so the sum of per-facet areas equals the total roof area with
no double counting and no XY-union "first facet swallows everything"
pathology that broke v3.2.

Lineage:
  v3   : sum of plane-local convex hulls → 30k sqft (way too high, overlap)
  v3.1 : + coplanar merge + spurious filter → 17k sqft (still 4.7x high)
  v3.2 : XY-union diff largest-first → 428 sqft (over-corrected, 1 facet ate the roof)
  v3.3 : reassign points → take per-facet XY hull → sum         (this version)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

try:
    import open3d as o3d
    HAS_OPEN3D = True
except ImportError:
    HAS_OPEN3D = False

LOG = logging.getLogger(__name__)


@dataclass
class RoofFacet:
    id: int
    plane_normal: Tuple[float, float, float]
    plane_d: float
    inlier_indices: np.ndarray
    area_m2: float
    pitch_deg: float
    pitch_x_in_12: float
    azimuth_deg: float
    centroid: Tuple[float, float, float]
    outline_xy: Optional[np.ndarray] = None


@dataclass
class RoofEdge:
    facet_a: int
    facet_b: Optional[int]
    length_m: float
    midpoint: Tuple[float, float, float]
    kind: str


@dataclass
class RoofSegmentation:
    facets: List[RoofFacet]
    edges: List[RoofEdge]
    ground_z: float
    point_count: int
    notes: List[str] = field(default_factory=list)


def _normal_to_pitch_azimuth(normal):
    nx, ny, nz = normal
    if nz < 0:
        nx, ny, nz = -nx, -ny, -nz
    pitch_deg = math.degrees(math.acos(max(-1.0, min(1.0, nz))))
    pitch_x_in_12 = math.tan(math.radians(pitch_deg)) * 12
    azimuth_rad = math.atan2(-nx, -ny)
    azimuth_deg = math.degrees(azimuth_rad) % 360
    return pitch_deg, pitch_x_in_12, azimuth_deg


def _adaptive_params(density, points_already_filtered):
    if density < 5:
        return 0.35, 25, False, 4.0, 18, 2000
    elif density < 8:
        return 0.30, 20, False, 2.5, 15, 1500
    elif density < 20:
        return 0.20, 25, "mild", 1.5, 20, 1500
    else:
        return 0.15, 30, True, 1.2, 30, 2000


def _facet_angle_diff_deg(n1, n2):
    n1 = np.asarray(n1) / (np.linalg.norm(n1) + 1e-12)
    n2 = np.asarray(n2) / (np.linalg.norm(n2) + 1e-12)
    return math.degrees(math.acos(float(np.clip(n1 @ n2, -1.0, 1.0))))


def _merge_coplanar_facets(facets, angle_tol_deg=8.0, centroid_xy_dist_m=10.0):
    n = len(facets)
    if n <= 1:
        return facets
    parent = list(range(n))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def union(i, j):
        pi, pj = find(i), find(j)
        if pi != pj:
            parent[pi] = pj
    for i in range(n):
        for j in range(i + 1, n):
            angle = _facet_angle_diff_deg(facets[i].plane_normal, facets[j].plane_normal)
            if angle > angle_tol_deg:
                continue
            ci = np.asarray(facets[i].centroid[:2])
            cj = np.asarray(facets[j].centroid[:2])
            if np.linalg.norm(ci - cj) <= centroid_xy_dist_m:
                union(i, j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    merged = []
    next_id = 0
    for _, indices in groups.items():
        if len(indices) == 1:
            f = facets[indices[0]]
            f.id = next_id
            merged.append(f)
            next_id += 1
            continue
        all_inlier_idx = np.unique(np.concatenate([facets[i].inlier_indices for i in indices]))
        rep_i = max(indices, key=lambda i: len(facets[i].inlier_indices))
        rep_normal = np.asarray(facets[rep_i].plane_normal)
        rep_d = facets[rep_i].plane_d
        pitch_deg, pitch_x12, azimuth_deg = _normal_to_pitch_azimuth(rep_normal)
        centroids_3d = np.array([facets[i].centroid for i in indices])
        weights = np.array([len(facets[i].inlier_indices) for i in indices], dtype=float)
        weights /= weights.sum()
        combined_centroid = tuple((centroids_3d * weights[:, None]).sum(axis=0).tolist())
        merged.append(RoofFacet(
            id=next_id, plane_normal=tuple(rep_normal.tolist()), plane_d=rep_d,
            inlier_indices=all_inlier_idx, area_m2=0.0,  # recomputed later
            pitch_deg=pitch_deg, pitch_x_in_12=pitch_x12, azimuth_deg=azimuth_deg,
            centroid=combined_centroid, outline_xy=None,
        ))
        next_id += 1
    return merged


def _reassign_and_compute_areas(facets, roof_points, roof_idx_to_orig, max_dist_m=0.5):
    """v3.3: point-assignment-based area calc.

    For each roof point, compute distance to each facet's plane and assign
    the point to its single closest facet (if within `max_dist_m`).
    Per-facet area = convex hull of its newly-assigned XY positions,
    slope-corrected by 1/cos(pitch).

    Returns: (new_facets, total_area_m2)
    """
    if not facets or len(roof_points) == 0:
        return facets, 0.0
    try:
        from scipy.spatial import ConvexHull
    except ImportError:
        return facets, 0.0

    n_pts = len(roof_points)
    n_facets = len(facets)

    # Distance matrix: n_pts x n_facets, perpendicular distance to each plane
    dist = np.full((n_pts, n_facets), np.inf)
    for j, f in enumerate(facets):
        nrm = np.array(f.plane_normal)
        # plane: n . X = plane_d  (n is unit). |n . X - plane_d| is perpendicular distance.
        dist[:, j] = np.abs(roof_points @ nrm - f.plane_d)

    best_facet = np.argmin(dist, axis=1)
    best_dist = dist[np.arange(n_pts), best_facet]
    assigned_mask = best_dist < max_dist_m

    new_facets = []
    total_area_m2 = 0.0
    for j, f in enumerate(facets):
        member_mask = (best_facet == j) & assigned_mask
        member_idx = np.where(member_mask)[0]  # indices into roof_points
        if len(member_idx) < 4:
            continue
        member_pts = roof_points[member_idx]
        # Per-facet area = convex hull of assigned points in XY, slope-corrected
        xy = member_pts[:, :2]
        try:
            hull = ConvexHull(xy)
            xy_hull_area = float(hull.volume)  # 2D area
        except Exception:
            # Fallback to bbox
            mn, mx = xy.min(axis=0), xy.max(axis=0)
            xy_hull_area = float((mx[0] - mn[0]) * (mx[1] - mn[1]))
        slope = max(math.cos(math.radians(f.pitch_deg)), 0.05)
        area_m2 = xy_hull_area / slope
        f.area_m2 = area_m2
        f.inlier_indices = roof_idx_to_orig[member_idx]
        f.centroid = tuple(member_pts.mean(axis=0).tolist())
        try:
            f.outline_xy = xy[hull.vertices]
        except Exception:
            f.outline_xy = None
        total_area_m2 += area_m2
        new_facets.append(f)

    new_facets.sort(key=lambda f: -f.area_m2)
    for i, f in enumerate(new_facets):
        f.id = i
    return new_facets, total_area_m2


def _filter_spurious_facets(facets, min_pitch_deg=5.0, max_area_m2=600.0,
                            min_area_m2=2.0):
    """v3.3: relaxed max (was 400) since point-assignment naturally bounds it,
    plus added min_area to drop tiny noise facets (<2m^2 = <21 sqft)."""
    kept, dropped = [], []
    for f in facets:
        if f.pitch_deg < min_pitch_deg:
            dropped.append(f"#{f.id} flat (pitch={f.pitch_deg:.1f}deg)")
            continue
        if f.area_m2 > max_area_m2:
            dropped.append(f"#{f.id} oversize ({f.area_m2:.0f}m^2)")
            continue
        if f.area_m2 < min_area_m2:
            dropped.append(f"#{f.id} tiny ({f.area_m2:.1f}m^2)")
            continue
        kept.append(f)
    for new_id, f in enumerate(kept):
        f.id = new_id
    return kept, dropped


def estimate_density(points):
    if len(points) < 4:
        return 0.0
    ex = float(np.percentile(points[:, 0], 99) - np.percentile(points[:, 0], 1))
    ey = float(np.percentile(points[:, 1], 99) - np.percentile(points[:, 1], 1))
    return len(points) / max(1.0, ex * ey)


def segment_roof(
    points, *,
    density_hint=None, points_already_filtered=False,
    ground_z_percentile=1.0, min_hag_m=1.0, max_planes=16, verbose=False,
    plane_threshold_m=None, plane_min_inliers=None, use_outlier_filter=None,
    cluster_eps_m=None, cluster_min_points=None,
    footprint_area_m2=None,
    footprint_vertex_count=None,
):
    if not HAS_OPEN3D:
        return RoofSegmentation([], [], 0.0, len(points), notes=["open3d not installed"])
    if len(points) < 15:
        return RoofSegmentation([], [], 0.0, len(points), notes=["too few input points"])

    density = density_hint if density_hint else estimate_density(points)
    LOG.info("seg_v3_3: density=%.1f pts/m^2", density)

    auto_thr, auto_min, auto_out, auto_eps, auto_clu_min, auto_iter = _adaptive_params(
        density, points_already_filtered)
    if plane_threshold_m is None: plane_threshold_m = auto_thr
    if plane_min_inliers is None: plane_min_inliers = auto_min
    if use_outlier_filter is None: use_outlier_filter = auto_out and not points_already_filtered
    if cluster_eps_m is None: cluster_eps_m = auto_eps
    if cluster_min_points is None: cluster_min_points = auto_clu_min

    LOG.info("seg_v3_3: thr=%.2fm min=%d out=%s eps=%.2fm clu=%d iter=%d",
             plane_threshold_m, plane_min_inliers, use_outlier_filter,
             cluster_eps_m, cluster_min_points, auto_iter)

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    current_to_orig = np.arange(len(points))

    if use_outlier_filter and len(points) > 200:
        try:
            if use_outlier_filter == "mild":
                pcd_clean, kept_local = pcd.remove_statistical_outlier(nb_neighbors=8, std_ratio=3.0)
            else:
                pcd_clean, kept_local = pcd.remove_statistical_outlier(nb_neighbors=15, std_ratio=2.0)
            LOG.info("seg_v3_3: outlier %d -> %d", len(pcd.points), len(pcd_clean.points))
            pcd = pcd_clean
            current_to_orig = current_to_orig[np.asarray(kept_local)]
        except Exception as e:
            LOG.warning("seg_v3_3: outlier removal failed: %s", e)

    pts_np = np.asarray(pcd.points)
    if points_already_filtered:
        ground_z = float(np.percentile(pts_np[:, 2], 0)) if len(pts_np) else 0.0
        pcd_roof = pcd
        roof_to_orig = current_to_orig
    else:
        z = pts_np[:, 2]
        ground_z = float(np.percentile(z, ground_z_percentile))
        roof_mask = z > (ground_z + min_hag_m)
        keep_local = np.where(roof_mask)[0]
        pcd_roof = pcd.select_by_index(keep_local.tolist())
        roof_to_orig = current_to_orig[keep_local]
        LOG.info("seg_v3_3: ground filter ground_z=%.2f kept %d/%d",
                 ground_z, len(pcd_roof.points), len(pcd.points))

    if len(pcd_roof.points) < plane_min_inliers:
        return RoofSegmentation([], [], ground_z, len(points),
                                notes=[f"only {len(pcd_roof.points)} pts"])

    # Keep a copy of the roof point cloud (for v3.3 reassignment)
    roof_points_np = np.asarray(pcd_roof.points)

    remaining = pcd_roof
    remaining_to_orig = roof_to_orig.copy()
    facets = []
    facet_id = 0
    rejection_log = []
    plane_idx = 0

    for plane_idx in range(max_planes):
        if len(remaining.points) < plane_min_inliers:
            break
        try:
            plane_model, inlier_indices = remaining.segment_plane(
                distance_threshold=plane_threshold_m, ransac_n=3, num_iterations=auto_iter)
        except Exception as e:
            rejection_log.append(f"iter{plane_idx}:{e}")
            break

        if len(inlier_indices) < plane_min_inliers:
            break

        a, b, c, d = plane_model
        normal = np.array([a, b, c], dtype=np.float64)
        norm_len = np.linalg.norm(normal)
        if norm_len == 0 or not np.isfinite(norm_len):
            keep_mask = np.ones(len(remaining_to_orig), dtype=bool)
            keep_mask[np.asarray(inlier_indices)] = False
            remaining = remaining.select_by_index(inlier_indices, invert=True)
            remaining_to_orig = remaining_to_orig[keep_mask]
            continue
        normal /= norm_len
        if normal[2] < 0:
            normal = -normal
        plane_d = -d / norm_len

        if normal[2] < 0.15:
            keep_mask = np.ones(len(remaining_to_orig), dtype=bool)
            keep_mask[np.asarray(inlier_indices)] = False
            remaining = remaining.select_by_index(inlier_indices, invert=True)
            remaining_to_orig = remaining_to_orig[keep_mask]
            continue

        inlier_points = np.asarray(remaining.points)[inlier_indices]
        inlier_pcd = remaining.select_by_index(inlier_indices)
        try:
            labels = np.array(inlier_pcd.cluster_dbscan(
                eps=cluster_eps_m, min_points=cluster_min_points, print_progress=False))
        except Exception:
            labels = np.zeros(len(inlier_indices), dtype=int)

        unique_labels = [l for l in np.unique(labels) if l >= 0]
        if not unique_labels:
            unique_labels = [0]
            labels = np.zeros(len(inlier_indices), dtype=int)

        for cluster_label in unique_labels:
            cluster_mask = labels == cluster_label
            cluster_points = inlier_points[cluster_mask]
            if len(cluster_points) < cluster_min_points:
                continue

            pitch_deg, pitch_x12, azimuth_deg = _normal_to_pitch_azimuth(normal)
            centroid = tuple(cluster_points.mean(axis=0).tolist())
            cluster_inlier_orig = remaining_to_orig[np.array(inlier_indices)[cluster_mask]]

            facets.append(RoofFacet(
                id=facet_id, plane_normal=tuple(normal.tolist()), plane_d=plane_d,
                inlier_indices=cluster_inlier_orig, area_m2=0.0,  # placeholder
                pitch_deg=pitch_deg, pitch_x_in_12=pitch_x12, azimuth_deg=azimuth_deg,
                centroid=centroid, outline_xy=None,
            ))
            facet_id += 1

        keep_mask = np.ones(len(remaining_to_orig), dtype=bool)
        keep_mask[np.asarray(inlier_indices)] = False
        remaining = remaining.select_by_index(inlier_indices, invert=True)
        remaining_to_orig = remaining_to_orig[keep_mask]

    LOG.info("seg_v3_3: %d raw facets after %d iters", len(facets), plane_idx + 1)

    # v3.1 coplanar merge
    pre_merge = len(facets)
    facets = _merge_coplanar_facets(facets, angle_tol_deg=8.0, centroid_xy_dist_m=10.0)
    LOG.info("seg_v3_3: merge %d -> %d", pre_merge, len(facets))

    # v3.3 point reassignment + area recompute
    facets, total_area_m2 = _reassign_and_compute_areas(
        facets, roof_points_np, roof_to_orig, max_dist_m=0.5,
    )
    LOG.info("seg_v3_3: reassign -> %d facets, total_area=%.1fm^2 (%.0fsqft)",
             len(facets), total_area_m2, total_area_m2 * 10.7639)

    facets, dropped = _filter_spurious_facets(
        facets, min_pitch_deg=5.0, max_area_m2=600.0, min_area_m2=2.0,
    )
    if dropped:
        LOG.info("seg_v3_3: dropped %d: %s", len(dropped), "; ".join(dropped[:5]))

    notes = [
        f"v3.3 reassign density={density:.1f}/m^2 thr={plane_threshold_m}m min={plane_min_inliers}",
        f"merge {pre_merge}->{len(facets)+len(dropped)}, drop {len(dropped)}, final {len(facets)}",
        f"total_area={total_area_m2:.1f}m^2 ({total_area_m2*10.7639:.0f}sqft)",
    ]
    if dropped:
        notes.append("dropped: " + "; ".join(dropped[:3]))


    # v3.4: footprint-area override (OSM polygon is ground truth for XY area)
    if footprint_area_m2 and footprint_area_m2 > 5 and facets:
        import math as _math
        total_inliers = sum(len(f.inlier_indices) for f in facets)
        if total_inliers > 0:
            weighted_cos = sum(_math.cos(_math.radians(f.pitch_deg)) * len(f.inlier_indices) for f in facets) / total_inliers
            weighted_cos = max(weighted_cos, 0.1)
            v = footprint_vertex_count or 8
            if v <= 5: OVERHANG_FACTOR = 1.40
            elif v <= 7: OVERHANG_FACTOR = 1.25
            elif v <= 12: OVERHANG_FACTOR = 1.15
            else: OVERHANG_FACTOR = 1.10
            LOG.info('seg_v3_5: overhang=%.2f for %d-vert', OVERHANG_FACTOR, v)
            total_surface_m2 = footprint_area_m2 * OVERHANG_FACTOR / weighted_cos
            for f in facets:
                share = len(f.inlier_indices) / total_inliers
                f.area_m2 = total_surface_m2 * share
            LOG.info("seg_v3_5: footprint override fp=%.1fm^2 -> total_surface=%.1fm^2 (%.0fsqft)",
                     footprint_area_m2, total_surface_m2, total_surface_m2 * 10.7639)

    return RoofSegmentation(
        facets=facets, edges=[], ground_z=ground_z,
        point_count=len(points), notes=notes,
    )
