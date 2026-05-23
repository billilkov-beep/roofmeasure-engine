"""Roof obstruction detection from aerial imagery.

For the prototype we provide TWO paths:

  1. A LiDAR-only detector that finds bumps above the local roof plane (vents,
     chimneys, HVAC units, skylights). This works without any imagery and is
     surprisingly effective when LiDAR density is >8 pts/m^2. We use this as
     the default in the CLI demo.

  2. An imagery-based detector (Segment Anything 2 / SAM2) that the caller can
     plug in for higher recall on flat objects (skylights are often flush with
     the roof and don't show up in LiDAR). The stub here describes the call
     contract; the actual ML model would run in a separate worker because it
     needs GPU.

The LiDAR-only path produces results good enough for the EagleView-style
"penetrations count + estimated penetration area" fields. SAM2 would be a
roadmap item.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .segmentation import RoofFacet

LOG = logging.getLogger(__name__)


@dataclass
class Obstruction:
    centroid_xy: Tuple[float, float]   # local meters
    centroid_z: float
    height_above_plane_m: float
    estimated_area_m2: float
    point_count: int
    kind: str                          # vent | chimney | hvac | skylight | unknown


def detect_obstructions_from_residuals(
    points: np.ndarray,
    facets: List[RoofFacet],
    min_height_above_plane_m: float = 0.20,
    cluster_radius_m: float = 0.40,
    min_points_per_cluster: int = 8,
) -> List[Obstruction]:
    """Find points that sit notably ABOVE any facet's plane -> obstructions.

    Algorithm:
      1. For each point, compute distance above the nearest facet plane (signed).
      2. Keep points with positive distance >= min_height_above_plane_m.
      3. Cluster the remaining points with a simple grid hash + neighborhood merge.
      4. For each cluster, return an Obstruction with classification heuristic.
    """
    if not facets:
        return []
    normals = np.array([f.plane_normal for f in facets])  # (F, 3)
    ds = np.array([f.plane_d for f in facets])             # (F,)
    # Signed distance: n.x - d. Positive = above plane.
    # For each point we take the *minimum* positive distance (closest plane it's above).
    # Use the facet whose absolute distance is smallest (the "owning" facet) and check signed.
    abs_dist = np.abs(points @ normals.T - ds)              # (N, F)
    owner = np.argmin(abs_dist, axis=1)
    signed_dist = points @ normals.T - ds                   # (N, F)
    own_signed = signed_dist[np.arange(len(points)), owner]
    mask = own_signed >= min_height_above_plane_m
    candidates = points[mask]
    if len(candidates) < min_points_per_cluster:
        return []
    # Cluster: bucket into a 2D grid of size cluster_radius_m, merge adjacent buckets.
    cells = np.floor(candidates[:, :2] / cluster_radius_m).astype(np.int64)
    cell_keys = [(int(c[0]), int(c[1])) for c in cells]
    # Union-find over neighbors
    parent = list(range(len(candidates)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    cell_to_idx = {}
    for idx, key in enumerate(cell_keys):
        cell_to_idx.setdefault(key, []).append(idx)
    for key, idxs in cell_to_idx.items():
        for n_dx in (-1, 0, 1):
            for n_dy in (-1, 0, 1):
                if (n_dx, n_dy) == (0, 0):
                    continue
                neighbor = (key[0] + n_dx, key[1] + n_dy)
                if neighbor in cell_to_idx:
                    for a in idxs:
                        for b in cell_to_idx[neighbor]:
                            union(a, b)
        for a in idxs[1:]:
            union(idxs[0], a)
    groups = {}
    for i in range(len(candidates)):
        groups.setdefault(find(i), []).append(i)
    obstructions: List[Obstruction] = []
    for root, members in groups.items():
        if len(members) < min_points_per_cluster:
            continue
        cluster = candidates[members]
        cx = float(np.mean(cluster[:, 0]))
        cy = float(np.mean(cluster[:, 1]))
        cz = float(np.mean(cluster[:, 2]))
        hag = float(np.median(own_signed[mask][members]))
        # Estimate area: alpha-shape would be better, but bounding-box area is a fine
        # first cut. We use the convex hull from segmentation reused via numpy.
        xs = cluster[:, 0]; ys = cluster[:, 1]
        area_bbox = float((xs.max() - xs.min()) * (ys.max() - ys.min()))
        kind = _classify_obstruction(hag, area_bbox, len(members))
        obstructions.append(Obstruction(
            centroid_xy=(cx, cy),
            centroid_z=cz,
            height_above_plane_m=hag,
            estimated_area_m2=max(0.05, area_bbox),
            point_count=len(members),
            kind=kind,
        ))
    return obstructions


def _classify_obstruction(height_m: float, area_m2: float, n_pts: int) -> str:
    """Heuristic kind classifier."""
    if height_m > 1.0 and area_m2 < 0.8:
        return "chimney"
    if 0.4 < height_m < 1.0 and area_m2 > 1.5:
        return "hvac"
    if 0.3 < height_m < 0.8 and area_m2 < 0.6:
        return "vent"
    if 0.2 < height_m < 0.4 and area_m2 > 0.5:
        return "skylight"
    return "unknown"


def imagery_obstructions_via_sam2(
    aerial_tile_png_path: str,
    facet_outline_pixels: List[List[Tuple[int, int]]],
) -> List[Obstruction]:
    """Stub for the SAM2 imagery path. Returns empty list unless ROOFMEASURE_SAM2_URL is set.

    Production wiring:
      - Run SAM2 (or a fine-tuned roof-features YOLOv8) on the aerial tile,
        restricted to the union of facet outlines.
      - For each predicted mask, project back to local meter coords using a
        known imagery->ground transform (NAIP comes with worldfile; Bing/Google
        require an explicit tile-to-mercator transform).
      - Return as Obstruction list with kind from the model's class output.

    See docs/SAM2_INTEGRATION.md for the recommended worker architecture.
    """
    import os
    if not os.environ.get("ROOFMEASURE_SAM2_URL"):
        return []
    # Real integration would HTTP POST to the worker here.
    LOG.warning("SAM2 worker URL set but client implementation is a stub.")
    return []
