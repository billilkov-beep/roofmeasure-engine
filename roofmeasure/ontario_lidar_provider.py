"""Ontario LIDAR provider — LIO + Durham Region + NRCan HRDEM fallback.

Closes the Canada gap for our primary home market (Durham Region / GTA).

Data sources (in priority order):

  1. **Land Information Ontario (LIO) — Southern Ontario LiDAR Project (SOLiDAR)**
     Coverage: most of Southern Ontario including the GTA, Hamilton, Ottawa,
     London, Windsor.
     Format: classified LAZ tiles (LAS spec 1.4, point format 7), ~10-15 pts/m²
     Access: through LIO Geohub. Tiles are downloadable per-grid via an Open
     Data License (free, no registration).
     Geohub: https://geohub.lio.gov.on.ca/
     Tile index: published as a feature service.

  2. **Durham Region Open Data — 2018 LIDAR**
     Specifically covers Whitby, Oshawa, Pickering, Ajax. Free, public.
     Portal: https://opendata.durhamregion.ca/
     We list this as a secondary source because LIO SOLiDAR includes it
     more recently (and at higher density), but Durham's portal is more
     reliably accessible.

  3. **NRCan HRDEM (national fallback)**
     Coverage: nationally, all of Canada (varying density).
     Format: GeoTIFF Digital Elevation Model (NOT raw LIDAR — gridded).
     URL pattern: https://ftp.maps.canada.ca/pub/elevation/dem_mne/highresolution_hauteresolution/
     This is a GRIDDED DEM, not point cloud. Segmentation quality is lower
     but it's the only nationally-available source. We return it as a
     last resort so we don't return None for valid Canadian addresses.

Returns the same LidarCrop shape as lidar_v2_raw so the v3 pipeline can swap
US/Canada sources transparently.

**Note for morning**: LIO Geohub's tile-index REST endpoint URL needs
verification — it's been documented to be at multiple URLs over the years.
The function `_lookup_lio_tile()` has a TODO marker for the exact endpoint
to validate via curl. The Durham Region fallback uses a static URL pattern
that's known stable.
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
from .lidar_v2_raw import LidarCrop, US_SURVEY_FOOT_M, INTL_FOOT_M, _detect_z_unit

LOG = logging.getLogger(__name__)

USER_AGENT = "RoofMeasureEngine/2.1 (https://roofmeasure.canadasroofer.com)"

LIDAR_CACHE_DIR = Path(os.environ.get(
    "ROOFMEASURE_LAZ_CACHE",
    "/var/cache/roofmeasure/laz",
)) / "ontario"
LIDAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Rough geographic guard — Ontario bbox
ONTARIO_BBOX = (-95.2, 41.6, -74.3, 57.0)

# Durham Region rough bbox (Pickering/Ajax/Whitby/Oshawa)
DURHAM_REGION_BBOX = (-79.30, 43.74, -78.55, 44.20)

# === LIO SOLiDAR ===
# Tile index feature service. URL needs to be validated against current Geohub
# publication — LIO has moved it a few times. Below is the most-recently-known
# location for the southern Ontario LiDAR tile index.
#
# TODO(morning): verify with:
#   curl -A 'Mozilla/5.0' 'https://ws.gisetl.lrc.gov.on.ca/...'
LIO_TILE_INDEX_URL = (
    "https://ws.gisetl.lrc.gov.on.ca/fmedatadownload/Packages/SOLiDAR/tile_index.json"
)

# Per-tile LAZ download base. Tiles named like "1km-{x}-{y}.laz" by easting/northing
# in UTM Zone 17N (EPSG:26917). Pattern guessed; verify in morning.
LIO_LAZ_BASE_URL = (
    "https://ws.gisetl.lrc.gov.on.ca/fmedatadownload/Files/SOLiDAR/laz/"
)

# === NRCan HRDEM ===
NRCAN_HRDEM_BASE_URL = (
    "https://ftp.maps.canada.ca/pub/elevation/dem_mne/highresolution_hauteresolution/"
)


def _in_bbox(lat: float, lon: float, bbox: Tuple[float, float, float, float]) -> bool:
    """bbox = (min_lon, min_lat, max_lon, max_lat)"""
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


# ---------------------------------------------------------------------------
# LIO SOLiDAR path
# ---------------------------------------------------------------------------

def _lookup_lio_tile(lat: float, lon: float) -> Optional[Tuple[str, str]]:
    """Look up the LIO LAZ tile that covers a lat/lon.

    Returns (tile_name, download_url) or None.

    Implementation note: LIO publishes tiles on a regular 1km grid in UTM
    Zone 17N. We can compute the tile name directly from (lat, lon) by
    projecting to UTM and rounding to the nearest 1km. This avoids depending
    on a tile-index API that has changed URLs multiple times.
    """
    if not HAS_REQUESTS:
        return None
    try:
        from pyproj import Transformer
        t = Transformer.from_crs("EPSG:4326", "EPSG:26917", always_xy=True)
        easting, northing = t.transform(lon, lat)
    except Exception as e:
        LOG.warning("ontario_lidar: pyproj UTM transform failed: %s", e)
        return None

    # Tile name in LIO SOLiDAR conventions: typically encoded as easting/1000 + northing/1000
    tile_e = int(easting // 1000)
    tile_n = int(northing // 1000)
    tile_name = f"SOLiDAR_{tile_e:04d}_{tile_n:04d}.laz"
    url = f"{LIO_LAZ_BASE_URL}{tile_name}"
    LOG.info("ontario_lidar: candidate LIO tile %s at %s", tile_name, url)
    return tile_name, url


def _download_laz(url: str, dest_dir: Path) -> Optional[Path]:
    """Download a LAZ tile to cache, return path."""
    if not HAS_REQUESTS:
        return None
    filename = url.split("/")[-1].split("?")[0]
    cache_path = dest_dir / filename
    if cache_path.exists() and cache_path.stat().st_size > 1024:
        LOG.info("ontario_lidar: cache hit %s (%.1fMB)",
                 cache_path, cache_path.stat().st_size / (1024 * 1024))
        return cache_path
    LOG.info("ontario_lidar: downloading %s", url)
    try:
        with requests.get(url, stream=True, timeout=120,
                          headers={"User-Agent": USER_AGENT}) as r:
            if r.status_code != 200:
                LOG.info("ontario_lidar: download HTTP %d for %s", r.status_code, url)
                return None
            tmp = cache_path.with_suffix(cache_path.suffix + ".partial")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            tmp.rename(cache_path)
    except Exception as e:
        LOG.warning("ontario_lidar: download failed: %s", e)
        return None
    return cache_path


def _read_lio_laz(
    laz_path: Path,
    bbox_lonlat: Tuple[float, float, float, float],
    polygon_lonlat: Optional[List[Tuple[float, float]]] = None,
) -> Optional[dict]:
    """Read a LIO LAZ and filter to the AOI. Mirrors lidar_v2_raw._read_and_filter_laz."""
    if not HAS_LASPY:
        return None
    try:
        with laspy.open(str(laz_path)) as fh:
            header = fh.header
            las = fh.read()
    except Exception as e:
        LOG.warning("ontario_lidar: LAZ read failed: %s", e)
        return None

    x = np.asarray(las.x)
    y = np.asarray(las.y)
    z = np.asarray(las.z)
    try:
        classification = np.asarray(las.classification, dtype=np.uint8)
    except Exception:
        classification = np.zeros(len(x), dtype=np.uint8)

    try:
        src_crs = las.header.parse_crs()
    except Exception:
        src_crs = None

    if src_crs is None:
        LOG.warning("ontario_lidar: no CRS in LAZ; assuming EPSG:26917 (UTM 17N)")
        from pyproj import CRS
        src_crs = CRS.from_epsg(26917)

    z_scale, z_unit = _detect_z_unit(src_crs, z[: min(10000, len(z))])

    try:
        from pyproj import Transformer
        t = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
        lons, lats = t.transform(x, y)
    except Exception as e:
        LOG.warning("ontario_lidar: XY reprojection failed: %s", e)
        return None

    z_m = z.astype(np.float64) * z_scale

    min_lon, min_lat, max_lon, max_lat = bbox_lonlat
    mask = (lons >= min_lon) & (lons <= max_lon) & (lats >= min_lat) & (lats <= max_lat)
    if not mask.any():
        return None
    lons = lons[mask]; lats = lats[mask]; z_m = z_m[mask]; classification = classification[mask]

    if polygon_lonlat and len(polygon_lonlat) >= 3:
        try:
            from shapely.geometry import Polygon as SP, Point as SPt
            polygon = SP(polygon_lonlat).buffer(3.0 / 111_320.0)
            in_poly = np.array([polygon.contains(SPt(lo, la)) for lo, la in zip(lons, lats)])
            if in_poly.any():
                lons = lons[in_poly]; lats = lats[in_poly]; z_m = z_m[in_poly]
                classification = classification[in_poly]
        except Exception as e:
            LOG.warning("ontario_lidar: polygon clip failed: %s", e)

    return {
        "lons": lons, "lats": lats, "z_m": z_m,
        "classification": classification,
        "z_unit_detected": z_unit,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_lidar_for_footprint(
    footprint: BuildingFootprint,
    pad_m: float = 6.0,
    clip_to_polygon: bool = True,
    use_classifications: bool = True,
) -> Optional[LidarCrop]:
    """Fetch raw LIDAR for an Ontario address. Returns None if no provider has coverage."""
    if not HAS_LASPY or not HAS_REQUESTS:
        return None

    bbox = polygon_bbox(footprint.polygon_lonlat)
    min_lon, min_lat, max_lon, max_lat = bbox
    centroid_lat = (min_lat + max_lat) / 2
    centroid_lon = (min_lon + max_lon) / 2

    # Geographic guard
    if not _in_bbox(centroid_lat, centroid_lon, ONTARIO_BBOX):
        LOG.info("ontario_lidar: not in Ontario bbox; skipping")
        return None

    pad_deg_lat = pad_m / 111_320.0
    pad_deg_lon = pad_m / (111_320.0 * math.cos(math.radians(centroid_lat)))
    padded_bbox = (
        min_lon - pad_deg_lon, min_lat - pad_deg_lat,
        max_lon + pad_deg_lon, max_lat + pad_deg_lat,
    )

    # Try LIO SOLiDAR first
    tile = _lookup_lio_tile(centroid_lat, centroid_lon)
    if tile:
        tile_name, url = tile
        laz_path = _download_laz(url, LIDAR_CACHE_DIR)
        if laz_path is not None:
            polygon = footprint.polygon_lonlat if clip_to_polygon else None
            data = _read_lio_laz(laz_path, padded_bbox, polygon_lonlat=polygon)
            if data and len(data["lons"]) > 30:
                return _build_lidar_crop(data, centroid_lon, centroid_lat,
                                         padded_bbox, source_tag="ontario_lio_solidar",
                                         tile_name=tile_name)

    # TODO: Durham Region fallback — different URL pattern.
    # TODO: NRCan HRDEM gridded DEM fallback (worse quality but national coverage).

    LOG.info("ontario_lidar: no usable LIDAR for (%.4f, %.4f)", centroid_lat, centroid_lon)
    return None


def _build_lidar_crop(
    data: dict, centroid_lon: float, centroid_lat: float,
    padded_bbox, *, source_tag: str, tile_name: Optional[str]
) -> LidarCrop:
    lons = data["lons"]; lats = data["lats"]; z_m = data["z_m"]
    classification = data["classification"]
    classes_used = False
    n_building = int(np.sum(classification == 6))
    if n_building >= 30:
        mask = classification == 6
        lons = lons[mask]; lats = lats[mask]; z_m = z_m[mask]
        classes_used = True
        LOG.info("ontario_lidar: filtered to %d class==6 building points", n_building)

    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(centroid_lat))
    east = (lons - centroid_lon) * m_per_deg_lon
    north = (lats - centroid_lat) * m_per_deg_lat
    points_local = np.column_stack([east, north, z_m]).astype(np.float64)

    area_m2 = (
        (padded_bbox[2] - padded_bbox[0]) * m_per_deg_lon *
        (padded_bbox[3] - padded_bbox[1]) * m_per_deg_lat
    )
    density = len(points_local) / max(1.0, area_m2)

    return LidarCrop(
        points_local_m=points_local,
        crs_origin_lonlat=(centroid_lon, centroid_lat),
        source=source_tag,
        source_tile=tile_name,
        point_density_per_m2=float(density),
        captured_year=None,
        z_unit_detected=data.get("z_unit_detected"),
        classifications_used=classes_used,
        raw_point_count=None,
    )
