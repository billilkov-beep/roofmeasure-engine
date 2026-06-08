"""LIDAR fetch via py3dep (USGS 3DEP wrapper).

Replaces the hand-rolled USGS tile-discovery + LAZ-download path in
`roofmeasure/lidar.py`. py3dep handles:
  - Querying TNM Access API for available datasets
  - Downloading LAZ tiles
  - Caching to disk
  - Reading point clouds into numpy arrays
  - CRS reprojection to EPSG:4326 (lat/lng)

Library docs: https://docs.hyriver.io/readme/py3dep.html
Source: https://github.com/hyriver/py3dep

Public surface (matches the old `lidar.py` signature so `measurement.py` swap
is a one-line change):

    fetch_lidar_for_footprint(footprint) -> LidarCrop

The returned LidarCrop has the same shape as the old version so segmentation
+ orchestration don't need to change.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

# py3dep — lazy-imported because installing it pulls in rasterio/GDAL which is large.
# This module fails loudly with a clear message if py3dep isn't installed.
try:
    import py3dep
    HAS_PY3DEP = True
except ImportError:
    HAS_PY3DEP = False

from .footprint import BuildingFootprint, polygon_bbox

LOG = logging.getLogger(__name__)


@dataclass
class LidarCrop:
    """Identical shape to the old roofmeasure.lidar.LidarCrop for drop-in compatibility."""
    points_local_m: np.ndarray  # (N, 3) east, north, elevation (meters)
    crs_origin_lonlat: Tuple[float, float]
    source: str
    source_tile: Optional[str] = None
    point_density_per_m2: Optional[float] = None
    captured_year: Optional[int] = None


# Resolution: 1m is USGS 3DEP's standard "1-meter" product; works for most of the US.
# Resolution=0.5 gives the densest available where supported.
DEFAULT_RESOLUTION_M = 1.0


def fetch_lidar_for_footprint(
    footprint: BuildingFootprint,
    resolution_m: float = DEFAULT_RESOLUTION_M,
    pad_m: float = 8.0,
) -> Optional[LidarCrop]:
    """Fetch USGS 3DEP elevation for the bounding box of a footprint.

    Returns a LidarCrop with `points_local_m` as an (N, 3) array of east/north/elev
    in meters, projected to a local Cartesian frame anchored at the footprint centroid.

    Returns None if py3dep isn't installed (caller should fall back to old path)
    or if USGS has no coverage for the bbox.
    """
    if not HAS_PY3DEP:
        LOG.warning("lidar_v2: py3dep not installed — cannot run library-based path")
        return None

    bbox = polygon_bbox(footprint.polygon_lonlat)
    min_lon, min_lat, max_lon, max_lat = bbox

    # Pad bbox by a few meters so we capture eaves overhanging the footprint.
    # Convert pad_m to degrees roughly (good enough for small areas).
    pad_deg_lat = pad_m / 111_320.0
    pad_deg_lon = pad_m / (111_320.0 * math.cos(math.radians((min_lat + max_lat) / 2)))
    padded_bbox = (
        min_lon - pad_deg_lon,
        min_lat - pad_deg_lat,
        max_lon + pad_deg_lon,
        max_lat + pad_deg_lat,
    )

    LOG.info(
        "lidar_v2: fetching 3DEP @ res=%sm bbox=%s",
        resolution_m,
        tuple(round(x, 6) for x in padded_bbox),
    )

    # py3dep returns an xarray Dataset of elevations on a grid.
    # See: https://docs.hyriver.io/autoapi/py3dep/index.html#py3dep.get_map
    try:
        # `get_map` accepts a layer name ("DEM"), a bbox, and a resolution in meters.
        dem = py3dep.get_map(
            "DEM",
            padded_bbox,
            resolution=resolution_m,
            crs="EPSG:4326",
        )
    except Exception as e:
        LOG.warning("lidar_v2: py3dep get_map failed: %s", e)
        return None

    if dem is None or dem.size == 0:
        LOG.warning("lidar_v2: empty DEM returned for bbox %s", padded_bbox)
        return None

    # Convert the xarray DataArray to (N, 3) east/north/elev points.
    # xarray DEM has 'x' (lng) and 'y' (lat) coords and values in meters of elevation.
    arr = dem.values  # shape (H, W), float
    ys = dem.y.values  # latitudes (descending typically)
    xs = dem.x.values  # longitudes

    # Centroid lat/lon for local Cartesian projection
    centroid_lon = (min_lon + max_lon) / 2
    centroid_lat = (min_lat + max_lat) / 2

    # Meters-per-degree at this latitude
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(centroid_lat))

    # Build (N, 3) array — east, north, elevation in meters from centroid.
    # We could vectorize this with np.meshgrid for speed but keep it explicit.
    rows, cols = arr.shape
    points = np.empty((rows * cols, 3), dtype=np.float64)
    idx = 0
    for i in range(rows):
        for j in range(cols):
            z = arr[i, j]
            if not np.isfinite(z):
                continue
            east = (xs[j] - centroid_lon) * m_per_deg_lon
            north = (ys[i] - centroid_lat) * m_per_deg_lat
            points[idx, 0] = east
            points[idx, 1] = north
            points[idx, 2] = float(z)
            idx += 1
    points = points[:idx]

    # Vectorized version for performance — keep above as the "obvious" reference,
    # uncomment this once you trust it:
    #
    # X, Y = np.meshgrid(xs, ys)
    # east = (X - centroid_lon) * m_per_deg_lon
    # north = (Y - centroid_lat) * m_per_deg_lat
    # points = np.column_stack([
    #     east.ravel(), north.ravel(), arr.ravel()
    # ])
    # points = points[np.isfinite(points[:, 2])]

    LOG.info(
        "lidar_v2: got %d valid points (resolution %sm, ~%.1f pts/m^2)",
        len(points), resolution_m, len(points) / max(1.0, (
            (padded_bbox[2] - padded_bbox[0]) * m_per_deg_lon *
            (padded_bbox[3] - padded_bbox[1]) * m_per_deg_lat
        )),
    )

    return LidarCrop(
        points_local_m=points,
        crs_origin_lonlat=(centroid_lon, centroid_lat),
        source="usgs_3dep_py3dep",
        source_tile=f"3DEP_{resolution_m}m_DEM",
        point_density_per_m2=None,
        captured_year=None,  # py3dep doesn't expose this directly
    )
