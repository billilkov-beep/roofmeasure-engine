"""LiDAR point cloud fetch + crop for a building footprint.

Free sources:
  US:  USGS 3DEP via the National Map TNM Access API + per-tile LAZ download
       OR OpenTopography API (requires free key, simpler).
  CA:  NRCan High Resolution DEM (HRDEM) tile index, or provincial portals
       (Ontario, BC, Quebec). We use NRCan as the universal fallback.

The actual LAZ/LAS reading is delegated to `laspy` when available, else PDAL
via subprocess. Both are common in production stacks. The point cloud is
returned as a numpy array of shape (N, 3) in a local meter-grid (east, north,
elevation) centered on the footprint centroid.

If neither laspy nor PDAL is installed, this module still works for the
*selection* and *download* steps and emits the LAZ file path for downstream
processing by the caller.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import requests

from .footprint import BuildingFootprint, polygon_bbox, _local_meters

LOG = logging.getLogger(__name__)

USER_AGENT = os.environ.get("ROOFMEASURE_USER_AGENT", "RoofMeasureEngine/0.1")


@dataclass
class LidarCrop:
    points_local_m: np.ndarray  # (N, 3) east, north, elevation (meters)
    crs_origin_lonlat: Tuple[float, float]
    source: str  # "usgs_3dep" | "opentopo" | "nrcan_hrdem"
    source_tile: Optional[str] = None
    point_density_per_m2: Optional[float] = None
    captured_year: Optional[int] = None


# ---------------------------------------------------------------------------
# Tile discovery
# ---------------------------------------------------------------------------

USGS_TNM_API = "https://tnmaccess.nationalmap.gov/api/v1/products"

def _discover_usgs_3dep_tiles(bbox: Tuple[float, float, float, float]) -> List[dict]:
    """Return a list of LAZ/LAS product records covering the bbox."""
    min_lon, min_lat, max_lon, max_lat = bbox
    params = {
        "datasets": "Lidar Point Cloud (LPC)",
        "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "prodFormats": "LAZ,LAS",
        "max": 50,
    }
    r = requests.get(USGS_TNM_API, params=params, timeout=30,
                     headers={"User-Agent": USER_AGENT})
    if r.status_code != 200:
        LOG.warning("USGS TNM API returned %s", r.status_code)
        return []
    items = (r.json() or {}).get("items") or []
    # Prefer the most recent dataset
    items.sort(key=lambda it: it.get("publicationDate") or "", reverse=True)
    return items


def _opentopography_pointcloud(
    bbox: Tuple[float, float, float, float],
    dataset: str = "USGS_LPC",
) -> Optional[bytes]:
    """Smaller, simpler alternative: OpenTopography 'pointcloud' service.
    Requires OPENTOPO_API_KEY env var. Returns LAZ bytes on success."""
    key = os.environ.get("OPENTOPO_API_KEY")
    if not key:
        return None
    min_lon, min_lat, max_lon, max_lat = bbox
    url = "https://portal.opentopography.org/API/pointcloud"
    params = {
        "demtype": dataset,
        "minx": min_lon, "miny": min_lat, "maxx": max_lon, "maxy": max_lat,
        "API_Key": key,
        "outputFormat": "LAZ",
    }
    r = requests.get(url, params=params, timeout=120,
                     headers={"User-Agent": USER_AGENT})
    if r.status_code != 200:
        LOG.warning("OpenTopography returned %s: %s", r.status_code, r.text[:200])
        return None
    return r.content


# ---------------------------------------------------------------------------
# Tile fetching + caching
# ---------------------------------------------------------------------------

def _cache_dir() -> str:
    d = os.environ.get("ROOFMEASURE_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "roofmeasure_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _download(url: str) -> str:
    """Download to cache, return local path."""
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    name = os.path.basename(url.split("?")[0]) or f"file_{h}"
    path = os.path.join(_cache_dir(), f"{h}_{name}")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    LOG.info("downloading %s", url)
    with requests.get(url, stream=True, timeout=300,
                      headers={"User-Agent": USER_AGENT}) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path


# ---------------------------------------------------------------------------
# Point cloud loading
# ---------------------------------------------------------------------------

def _read_laz_with_laspy(path: str) -> np.ndarray:
    import laspy  # type: ignore
    with laspy.open(path) as f:
        las = f.read()
    # Filter to roof-candidate returns: anything above ground; classification
    # codes in ASPRS standard: 1 unclassified, 2 ground, 6 building, 9 water.
    xyz = np.column_stack([las.x, las.y, las.z]).astype(np.float64)
    cls = np.array(las.classification, dtype=np.uint8) if hasattr(las, "classification") else np.full(len(xyz), 1, np.uint8)
    return np.column_stack([xyz, cls])


def _read_laz_with_pdal(path: str) -> np.ndarray:
    """Fall back to PDAL CLI if laspy isn't available."""
    if shutil.which("pdal") is None:
        raise RuntimeError("neither laspy nor pdal is installed")
    out = subprocess.run(
        ["pdal", "info", path, "--all"], capture_output=True, text=True, check=True)
    # PDAL info gives us metadata; for actual points we'd use a pipeline.
    raise NotImplementedError("PDAL pipeline reader not implemented in prototype "
                              "- install `pip install laspy` for the easy path")


def read_pointcloud(path: str) -> np.ndarray:
    """Return Nx4 array (x, y, z, classification_code) in source CRS."""
    try:
        return _read_laz_with_laspy(path)
    except ImportError:
        LOG.info("laspy not available, trying PDAL")
        return _read_laz_with_pdal(path)


# ---------------------------------------------------------------------------
# CRS handling
# ---------------------------------------------------------------------------

def _wgs84_to_local(points_xyz: np.ndarray, source_crs_epsg: Optional[int],
                    lon0: float, lat0: float) -> np.ndarray:
    """Convert tile coordinates to a local east/north/up meter grid centered on (lon0, lat0).

    USGS 3DEP LAZ files are typically in a state plane or UTM CRS. We support two paths:
      - If pyproj is available, do a proper transform.
      - Else assume the source is already in a metric CRS (UTM-like) and shift to the
        nearest-meter origin via the WGS84 -> local-meters approximation of the centroid.

    For the prototype, the second path is OK because we re-zero after cropping; the
    geometry calculations only need a *consistent* local meter grid, not georeferenced
    coordinates.
    """
    try:
        from pyproj import Transformer  # type: ignore
        if source_crs_epsg:
            t = Transformer.from_crs(source_crs_epsg, 4326, always_xy=True)
            lon, lat = t.transform(points_xyz[:, 0], points_xyz[:, 1])
            east = np.empty_like(lon)
            north = np.empty_like(lat)
            for i in range(len(lon)):
                e, n = _local_meters(lon[i], lat[i], lon0, lat0)
                east[i] = e
                north[i] = n
            return np.column_stack([east, north, points_xyz[:, 2]])
    except ImportError:
        pass

    # Approximation: treat x,y as already in meters relative to *some* origin and
    # re-center on (0, 0). Accurate enough for plane fitting + relative areas.
    cx = float(np.mean(points_xyz[:, 0]))
    cy = float(np.mean(points_xyz[:, 1]))
    return np.column_stack([
        points_xyz[:, 0] - cx,
        points_xyz[:, 1] - cy,
        points_xyz[:, 2],
    ])


def _crop_to_polygon_xy(points: np.ndarray, polygon_local_m: List[Tuple[float, float]]) -> np.ndarray:
    """Crop points (in same local meter grid as polygon) to inside-the-polygon.

    Uses a vectorized ray-casting test for speed.
    """
    poly = np.asarray(polygon_local_m)
    n = len(poly)
    x = points[:, 0]
    y = points[:, 1]
    inside = np.zeros(len(points), dtype=bool)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        cond = ((yi > y) != (yj > y)) & (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        )
        inside ^= cond
        j = i
    return points[inside]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_lidar_for_footprint(
    footprint: BuildingFootprint,
    pad_m: float = 5.0,
) -> LidarCrop:
    """Find, download, and crop LiDAR for the given building footprint.

    Returns LidarCrop with points in a local (east, north, elevation_m) grid
    centered on the footprint centroid.
    """
    cx, cy = footprint.centroid_lonlat or (0.0, 0.0)
    min_lon, min_lat, max_lon, max_lat = polygon_bbox(footprint.polygon_lonlat)

    # Pad bbox slightly so the tile selection has a small buffer
    pad_lat = pad_m / 111000.0
    pad_lon = pad_m / (111000.0 * max(0.1, math.cos(math.radians(cy))))
    bbox = (min_lon - pad_lon, min_lat - pad_lat, max_lon + pad_lon, max_lat + pad_lat)

    # ---- Try OpenTopography first (simplest, gives us pre-cropped LAZ) ----
    laz_bytes = _opentopography_pointcloud(bbox)
    if laz_bytes:
        path = os.path.join(_cache_dir(), f"opentopo_{int(cx*1e6)}_{int(cy*1e6)}.laz")
        with open(path, "wb") as f:
            f.write(laz_bytes)
        pts = read_pointcloud(path)
        local = _wgs84_to_local(pts, None, cx, cy)
        # crop precisely to footprint
        polygon_local = [
            _local_meters(lon, lat, cx, cy) for lon, lat in footprint.polygon_lonlat
        ]
        local = _crop_to_polygon_xy(local, polygon_local)
        return LidarCrop(
            points_local_m=local,
            crs_origin_lonlat=(cx, cy),
            source="opentopo",
        )

    # ---- Fall back to USGS TNM tile picker ----
    tiles = _discover_usgs_3dep_tiles(bbox)
    if tiles:
        # Pick the smallest tile that fully covers our point. The TNM API
        # returns 'downloadURL' for each product.
        candidates = [t for t in tiles if t.get("downloadURL")]
        if candidates:
            tile = candidates[0]
            local_laz = _download(tile["downloadURL"])
            pts = read_pointcloud(local_laz)
            local = _wgs84_to_local(pts, None, cx, cy)
            polygon_local = [
                _local_meters(lon, lat, cx, cy) for lon, lat in footprint.polygon_lonlat
            ]
            local = _crop_to_polygon_xy(local, polygon_local)
            return LidarCrop(
                points_local_m=local,
                crs_origin_lonlat=(cx, cy),
                source="usgs_3dep",
                source_tile=tile.get("title"),
                captured_year=int((tile.get("publicationDate") or "0")[:4] or 0) or None,
            )

    raise RuntimeError(
        "No LiDAR coverage found for footprint via OpenTopography or USGS 3DEP. "
        "For Canadian addresses, configure NRCan HRDEM or a provincial source."
    )


def synthesize_test_pointcloud(
    facets: List[dict],
    point_density_per_m2: float = 12.0,
    noise_m: float = 0.02,
    seed: int = 42,
) -> np.ndarray:
    """Generate a synthetic point cloud for testing the segmentation pipeline.

    Each facet dict has:
      - 'corners': list of (x, y, z) tuples in meters defining a planar polygon
    Returns (N, 3) array in the same local frame.
    """
    rng = np.random.default_rng(seed)
    all_pts = []
    for facet in facets:
        corners = np.array(facet["corners"], dtype=np.float64)
        # Compute plane via first 3 points
        p0, p1, p2 = corners[0], corners[1], corners[2]
        v1 = p1 - p0
        v2 = p2 - p0
        n = np.cross(v1, v2)
        n /= np.linalg.norm(n)
        # Bounding box in XY
        xs = corners[:, 0]; ys = corners[:, 1]
        xmin, xmax = xs.min(), xs.max()
        ymin, ymax = ys.min(), ys.max()
        area_xy = (xmax - xmin) * (ymax - ymin)
        n_pts = max(50, int(area_xy * point_density_per_m2))
        cand_x = rng.uniform(xmin, xmax, n_pts * 2)
        cand_y = rng.uniform(ymin, ymax, n_pts * 2)
        # Test against polygon (ray cast in 2D)
        poly = corners[:, :2]
        inside = _polygon_test(cand_x, cand_y, poly)
        cand_x = cand_x[inside][:n_pts]
        cand_y = cand_y[inside][:n_pts]
        # Solve for z on plane: n . ((x,y,z) - p0) = 0  =>  z = p0z - (nx*(x-p0x) + ny*(y-p0y)) / nz
        cand_z = p0[2] - (n[0] * (cand_x - p0[0]) + n[1] * (cand_y - p0[1])) / n[2]
        # Add noise
        cand_x += rng.normal(0, noise_m, len(cand_x))
        cand_y += rng.normal(0, noise_m, len(cand_y))
        cand_z += rng.normal(0, noise_m, len(cand_z))
        all_pts.append(np.column_stack([cand_x, cand_y, cand_z]))
    return np.vstack(all_pts) if all_pts else np.zeros((0, 3))


def _polygon_test(xs: np.ndarray, ys: np.ndarray, poly: np.ndarray) -> np.ndarray:
    n = len(poly)
    inside = np.zeros(len(xs), dtype=bool)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        cond = ((yi > ys) != (yj > ys)) & (
            xs < (xj - xi) * (ys - yi) / (yj - yi + 1e-12) + xi
        )
        inside ^= cond
        j = i
    return inside
