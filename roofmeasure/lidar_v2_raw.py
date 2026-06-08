"""Raw LIDAR fetch — v2.1 with unit fix, LAS classifications, polygon clip.

Changes from week1-migration-v2/lidar_v2_raw.py (which got 4622 points but 0 facets on Bedford):

  1. **Z-units fix (CRITICAL)**: The LAZ XYZ is in the source CRS's units —
     usually meters for UTM, but typically US Survey FEET for state plane in
     Texas, Florida, much of the southern US. Previously we projected XY to
     lat/lon → local meters but left Z in source units. If Z was in feet, the
     point cloud had MIXED UNITS — XY in meters, Z in feet. RANSAC plane
     thresholds (0.15m) became meaningless. This is almost certainly why
     Bedford TX returned 0 facets despite 4622 raw points.

     Fix: detect the source CRS linear unit and convert Z to meters
     explicitly. Also log range/unit so we can verify in the smoke test.

  2. **LAS classifications**: The ASPRS LAS spec defines classification
     codes (2=Ground, 6=Building). Modern USGS 3DEP collections include
     these. If the LAZ has buildings classified, we use ONLY class==6 points
     and skip the ground filter + outlier filter entirely. Much higher
     quality input to segmentation.

  3. **Polygon clip**: Previously filtered LAZ points by footprint BBOX.
     Now also filter by the actual footprint polygon (shapely contains)
     after Z-units fix. Cuts out neighboring buildings + driveways.
     Optional — controlled by `clip_to_polygon=True` parameter.

  4. **Captured-year**: Cleaner extraction from the TNM `dateCreated` field.

  5. **More verbose logging**: Each step reports counts so we can see
     exactly what's happening.

Public API unchanged: `fetch_lidar_for_footprint(footprint) -> LidarCrop | None`
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

try:
    import laspy
    HAS_LASPY = True
except ImportError:
    HAS_LASPY = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from .footprint_v2 import BuildingFootprint, polygon_bbox

LOG = logging.getLogger(__name__)

TNM_API_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"
LAZ_CACHE_DIR = Path(os.environ.get(
    "ROOFMEASURE_LAZ_CACHE",
    "/var/cache/roofmeasure/laz",
))
LAZ_CACHE_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "RoofMeasureEngine/2.1 (https://roofmeasure.canadasroofer.com)"

MAX_TILE_SIZE_MB = 800
DEFAULT_PAD_M = 6.0

# US Survey Foot to meter conversion (CRS axis units)
US_SURVEY_FOOT_M = 1200.0 / 3937.0  # exact
INTL_FOOT_M = 0.3048  # exact


@dataclass
class LidarCrop:
    """Drop-in compatible with the existing lidar.LidarCrop + v2 LidarCrop."""
    points_local_m: np.ndarray  # (N, 3) east, north, elevation (all meters)
    crs_origin_lonlat: Tuple[float, float]
    source: str
    source_tile: Optional[str] = None
    point_density_per_m2: Optional[float] = None
    captured_year: Optional[int] = None
    # New in v2.1: provenance for debugging
    z_unit_detected: Optional[str] = None  # "metre" | "US survey foot" | "foot"
    classifications_used: bool = False  # True if we filtered to class==6
    raw_point_count: Optional[int] = None  # before any filtering


# ---------------------------------------------------------------------------
# TNM Access API tile discovery
# ---------------------------------------------------------------------------

def _query_tnm_for_lpc(bbox_lonlat: Tuple[float, float, float, float]) -> List[dict]:
    if not HAS_REQUESTS:
        LOG.error("lidar_v2_raw: requests not installed")
        return []
    min_lon, min_lat, max_lon, max_lat = bbox_lonlat
    params = {
        "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "datasets": "Lidar Point Cloud (LPC)",
        "outputFormat": "JSON",
        "max": 50,
    }
    try:
        r = requests.get(TNM_API_URL, params=params, timeout=30,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        LOG.warning("lidar_v2_raw: TNM query failed: %s", e)
        return []
    items = data.get("items", []) or []
    LOG.info("lidar_v2_raw: TNM returned %d LPC products", len(items))
    return items


def _pick_best_tile(items: List[dict]) -> Optional[dict]:
    candidates = []
    for it in items:
        url = it.get("downloadURL") or ""
        if not url.lower().endswith(".laz"):
            continue
        size_b = it.get("sizeInBytes") or 0
        size_mb = size_b / (1024 * 1024)
        if size_mb > MAX_TILE_SIZE_MB:
            continue
        candidates.append((it, size_mb))
    if not candidates:
        return None
    candidates.sort(
        key=lambda pair: (pair[0].get("dateCreated") or "", -pair[1]),
        reverse=True,
    )
    chosen, size_mb = candidates[0]
    LOG.info("lidar_v2_raw: chose '%s' (%.1fMB, %s)",
             chosen.get("title", "?"), size_mb, chosen.get("dateCreated", "?"))
    return chosen


def _download_laz(url: str) -> Optional[Path]:
    if not HAS_REQUESTS:
        return None
    filename = url.split("/")[-1].split("?")[0]
    cache_path = LAZ_CACHE_DIR / filename
    if cache_path.exists() and cache_path.stat().st_size > 1024:
        LOG.info("lidar_v2_raw: cache hit %s (%.1fMB)",
                 cache_path, cache_path.stat().st_size / (1024 * 1024))
        return cache_path
    LOG.info("lidar_v2_raw: downloading %s", url)
    try:
        with requests.get(url, stream=True, timeout=120,
                          headers={"User-Agent": USER_AGENT}) as r:
            r.raise_for_status()
            tmp = cache_path.with_suffix(cache_path.suffix + ".partial")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            tmp.rename(cache_path)
    except Exception as e:
        LOG.warning("lidar_v2_raw: download failed: %s", e)
        return None
    return cache_path


# ---------------------------------------------------------------------------
# LAZ reading with unit detection + classification
# ---------------------------------------------------------------------------

def _detect_z_unit(src_crs, sample_z: np.ndarray) -> Tuple[float, str]:
    """Return (z_scale_to_meters, unit_name).

    Strategy:
      1. Look at the CRS axis info. If it reports 'foot' or 'US survey foot',
         return the exact conversion factor.
      2. If CRS is missing or ambiguous, fall back to heuristic on the Z range.
         Buildings span 4-15m vertically. If the elevation range is 50-200, it's
         almost certainly feet (US elevations); if it's 0-50, meters.
    """
    # Try CRS-based detection first
    try:
        units = set()
        for axis in src_crs.axis_info:
            if axis.unit_name:
                units.add(axis.unit_name.lower())
        if "us survey foot" in units or "ussurveyfoot" in units:
            return US_SURVEY_FOOT_M, "US survey foot"
        if "foot" in units:
            return INTL_FOOT_M, "foot"
        if "metre" in units or "meter" in units:
            return 1.0, "metre"
    except Exception:
        pass

    # Heuristic fallback
    z_range = float(np.percentile(sample_z, 99) - np.percentile(sample_z, 1))
    if z_range > 60:  # >60 raw units of variation in a small AOI = feet
        LOG.info("lidar_v2_raw: heuristic detected Z in feet (range=%.1f)", z_range)
        return US_SURVEY_FOOT_M, "US survey foot (heuristic)"
    LOG.info("lidar_v2_raw: heuristic detected Z in meters (range=%.1f)", z_range)
    return 1.0, "metre (heuristic)"


def _read_and_filter_laz(
    laz_path: Path,
    bbox_lonlat: Tuple[float, float, float, float],
    polygon_lonlat: Optional[List[Tuple[float, float]]] = None,
) -> Optional[dict]:
    """Read LAZ, reproject XY to lat/lon, fix Z units, return arrays.

    Returns dict:
      lons: (N,) longitudes (degrees)
      lats: (N,) latitudes (degrees)
      z_m:  (N,) elevations (meters)
      classification: (N,) uint8 LAS classifications (0 if not available)
      z_unit_detected: str
    """
    if not HAS_LASPY:
        LOG.error("lidar_v2_raw: laspy not installed")
        return None

    try:
        with laspy.open(str(laz_path)) as fh:
            header = fh.header
            las = fh.read()
    except Exception as e:
        LOG.warning("lidar_v2_raw: read failed %s: %s", laz_path, e)
        return None

    raw_count = header.point_count
    LOG.info("lidar_v2_raw: LAZ %s has %d points", laz_path.name, raw_count)

    x = np.asarray(las.x)
    y = np.asarray(las.y)
    z = np.asarray(las.z)

    # Classification (LAS spec: 2=Ground, 6=Building, 7=Low noise, etc.)
    try:
        classification = np.asarray(las.classification, dtype=np.uint8)
    except Exception:
        classification = np.zeros(len(x), dtype=np.uint8)

    # Parse source CRS
    src_crs = None
    try:
        src_crs = las.header.parse_crs()
    except Exception as e:
        LOG.warning("lidar_v2_raw: parse_crs failed: %s", e)

    if src_crs is None:
        LOG.warning("lidar_v2_raw: no CRS in LAZ; assuming EPSG:4326 (results may be wrong)")
        lons, lats = x.astype(np.float64), y.astype(np.float64)
        z_scale, z_unit = 1.0, "metre (no CRS — assumed)"
    else:
        # Detect Z unit BEFORE reprojection
        z_scale, z_unit = _detect_z_unit(src_crs, z[: min(10000, len(z))])
        LOG.info("lidar_v2_raw: detected Z unit = %s (scale=%.6f)", z_unit, z_scale)
        # XY reprojection
        try:
            from pyproj import Transformer
            transformer = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
            lons, lats = transformer.transform(x, y)
        except Exception as e:
            LOG.warning("lidar_v2_raw: XY reprojection failed: %s", e)
            return None

    # Apply Z scale to convert to meters
    z_m = z.astype(np.float64) * z_scale

    # 1. BBOX filter
    min_lon, min_lat, max_lon, max_lat = bbox_lonlat
    mask = (lons >= min_lon) & (lons <= max_lon) & (lats >= min_lat) & (lats <= max_lat)
    n_bbox = int(mask.sum())
    LOG.info("lidar_v2_raw: %d/%d points within bbox", n_bbox, len(lons))
    if n_bbox == 0:
        return None

    lons = lons[mask]
    lats = lats[mask]
    z_m = z_m[mask]
    classification = classification[mask]

    # 2. Optional polygon clip (point-in-polygon)
    if polygon_lonlat and len(polygon_lonlat) >= 3:
        try:
            from shapely.geometry import Polygon as SPolygon, Point as SPoint
            # Expand polygon slightly to catch eaves
            polygon = SPolygon(polygon_lonlat)
            # Use polygon.buffer with a small degree offset (~3m)
            buf_deg = 3.0 / 111_320.0  # ~3m
            buffered = polygon.buffer(buf_deg)
            # Vectorize via shapely 2.0 STRtree if many points
            in_poly = np.array([buffered.contains(SPoint(lo, la)) for lo, la in zip(lons, lats)])
            n_poly = int(in_poly.sum())
            LOG.info("lidar_v2_raw: %d/%d points within polygon (buffered 3m)", n_poly, n_bbox)
            if n_poly > 0:
                lons = lons[in_poly]
                lats = lats[in_poly]
                z_m = z_m[in_poly]
                classification = classification[in_poly]
        except Exception as e:
            LOG.warning("lidar_v2_raw: polygon clip failed (using bbox only): %s", e)

    return {
        "lons": lons,
        "lats": lats,
        "z_m": z_m,
        "classification": classification,
        "z_unit_detected": z_unit,
        "raw_point_count": raw_count,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_lidar_for_footprint(
    footprint: BuildingFootprint,
    pad_m: float = DEFAULT_PAD_M,
    clip_to_polygon: bool = True,
    use_classifications: bool = True,
) -> Optional[LidarCrop]:
    """Fetch raw LIDAR around a footprint. Returns LidarCrop or None."""
    if not HAS_LASPY or not HAS_REQUESTS:
        LOG.error("lidar_v2_raw: missing deps (laspy=%s, requests=%s)",
                  HAS_LASPY, HAS_REQUESTS)
        return None

    bbox = polygon_bbox(footprint.polygon_lonlat)
    min_lon, min_lat, max_lon, max_lat = bbox
    centroid_lat = (min_lat + max_lat) / 2

    pad_deg_lat = pad_m / 111_320.0
    pad_deg_lon = pad_m / (111_320.0 * math.cos(math.radians(centroid_lat)))
    padded_bbox = (
        min_lon - pad_deg_lon, min_lat - pad_deg_lat,
        max_lon + pad_deg_lon, max_lat + pad_deg_lat,
    )

    items = _query_tnm_for_lpc(padded_bbox)
    if not items:
        return None
    chosen = _pick_best_tile(items)
    if chosen is None:
        return None
    laz_path = _download_laz(chosen["downloadURL"])
    if laz_path is None:
        return None

    polygon = footprint.polygon_lonlat if clip_to_polygon else None
    data = _read_and_filter_laz(laz_path, padded_bbox, polygon_lonlat=polygon)
    if data is None or len(data["lons"]) == 0:
        return None

    lons = data["lons"]
    lats = data["lats"]
    z_m = data["z_m"]
    classification = data["classification"]
    z_unit = data["z_unit_detected"]
    raw_count = data["raw_point_count"]

    # Try classification-based filter
    classifications_used = False
    if use_classifications:
        n_building = int(np.sum(classification == 6))
        if n_building >= 30:
            LOG.info(
                "lidar_v2_raw: using LAS classification — %d building points (of %d)",
                n_building, len(lons),
            )
            mask = classification == 6
            lons = lons[mask]
            lats = lats[mask]
            z_m = z_m[mask]
            classifications_used = True
        else:
            LOG.info(
                "lidar_v2_raw: no useful classifications (only %d class==6 points); "
                "falling back to geometric filtering downstream",
                n_building,
            )

    # Project to local Cartesian meters
    centroid_lon = (min_lon + max_lon) / 2
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(centroid_lat))
    east = (lons - centroid_lon) * m_per_deg_lon
    north = (lats - centroid_lat) * m_per_deg_lat
    points_local = np.column_stack([east, north, z_m]).astype(np.float64)

    # Density
    area_m2 = (
        (padded_bbox[2] - padded_bbox[0]) * m_per_deg_lon *
        (padded_bbox[3] - padded_bbox[1]) * m_per_deg_lat
    )
    density = len(points_local) / max(1.0, area_m2)

    # Year
    captured_year = None
    date_str = chosen.get("dateCreated", "")
    if date_str:
        try:
            captured_year = int(date_str[:4])
        except Exception:
            pass

    LOG.info(
        "lidar_v2_raw: %d pts, %.1f pts/m^2, z_unit=%s, year=%s, classifications_used=%s",
        len(points_local), density, z_unit, captured_year, classifications_used,
    )

    return LidarCrop(
        points_local_m=points_local,
        crs_origin_lonlat=(centroid_lon, centroid_lat),
        source="usgs_3dep_lpc_raw_v2_1",
        source_tile=chosen.get("title"),
        point_density_per_m2=float(density),
        captured_year=captured_year,
        z_unit_detected=z_unit,
        classifications_used=classifications_used,
        raw_point_count=raw_count,
    )
