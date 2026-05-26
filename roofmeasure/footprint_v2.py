"""Building footprint discovery via OSMnx + Microsoft Building Footprints.

Replaces the hand-rolled `roofmeasure/footprint.py` Overpass calls.

Strategy:
  1. Try OSMnx first — handles failover across multiple Overpass mirrors,
     sets proper User-Agent, retries on transient errors. Industry standard.
  2. If OSM returns nothing, fall back to Microsoft Building Footprints
     (downloaded per US state as needed, cached locally).
  3. Return a BuildingFootprint matching the original signature.

OSMnx: https://github.com/gboeing/osmnx
Microsoft Footprints: https://github.com/microsoft/USBuildingFootprints
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import osmnx as ox
    import geopandas as gpd
    from shapely.geometry import Point, Polygon, shape
    HAS_OSMNX = True
except ImportError:
    HAS_OSMNX = False

LOG = logging.getLogger(__name__)

# Cache for Microsoft Footprints state files. Stored as GeoJSON downloads.
# 130M+ buildings across the US; per-state files are 50-500MB.
MS_FOOTPRINTS_CACHE = Path("/var/cache/roofmeasure/ms-footprints")
MS_FOOTPRINTS_CACHE.mkdir(parents=True, exist_ok=True)

# Lat/lng → state lookup (rough — for routing to the right MS Footprints download).
# Source URLs follow the pattern:
#   https://usbuildingdata.blob.core.windows.net/usbuildings-v2/{state}.geojson.zip
# But the GitHub repo also publishes a CDN-friendly download per state.


@dataclass
class BuildingFootprint:
    """Drop-in compatible with the existing footprint.BuildingFootprint."""
    polygon_lonlat: List[Tuple[float, float]]  # closed ring of (lon, lat)
    source: str  # "osm" | "microsoft_us" | "microsoft_ca"
    centroid_lonlat: Tuple[float, float]
    osm_id: Optional[str] = None


def _osm_footprint(lat: float, lon: float, search_radius_m: float = 80) -> Optional[BuildingFootprint]:
    """Try OSM via OSMnx. OSMnx handles Overpass mirrors and User-Agent natively."""
    if not HAS_OSMNX:
        return None
    try:
        # Configure OSMnx to use a real User-Agent (Overpass blocks anonymous)
        ox.settings.user_agent = "RoofMeasureEngine/2.0 (https://roofmeasure.canadasroofer.com)"
        ox.settings.requests_timeout = 30
        # `features_from_point` queries OSM features within a radius of a point.
        # We want buildings: tag = {"building": True} matches all building=* values.
        gdf = ox.features_from_point(
            (lat, lon),
            tags={"building": True},
            dist=search_radius_m,
        )
    except Exception as e:
        LOG.warning("footprint_v2: OSMnx query failed: %s", e)
        return None

    if gdf is None or gdf.empty:
        return None

    # Filter to polygons (not points/lines)
    poly_rows = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if poly_rows.empty:
        return None

    # Pick the building containing the query point, else the closest one.
    pt = Point(lon, lat)
    candidates = poly_rows.copy()
    containing = candidates[candidates.geometry.contains(pt)]
    if not containing.empty:
        chosen = containing.iloc[0]
    else:
        candidates["_dist"] = candidates.geometry.distance(pt)
        chosen = candidates.sort_values("_dist").iloc[0]

    geom = chosen.geometry
    if geom.geom_type == "MultiPolygon":
        # Pick the biggest sub-polygon
        geom = max(geom.geoms, key=lambda g: g.area)
    poly = list(geom.exterior.coords)  # list of (lon, lat) tuples

    osm_id = None
    try:
        osm_id = str(chosen.name)
    except Exception:
        pass

    cent = geom.centroid
    return BuildingFootprint(
        polygon_lonlat=poly,
        source="osm",
        centroid_lonlat=(cent.x, cent.y),
        osm_id=osm_id,
    )


def _ms_footprint(lat: float, lon: float) -> Optional[BuildingFootprint]:
    """Microsoft Building Footprints lookup.

    This is a STUB for now — the full implementation needs:
      1. A lat/lng → state mapping (for US) or province (for Canada)
      2. On-demand download of the relevant state's GeoJSON file (one-time, ~50-500MB)
      3. Spatial indexing of polygons (R-tree via geopandas .sindex)
      4. Point-in-polygon + nearest-neighbor lookup

    For week 1 we keep it as a stub returning None — OSMnx now handles the
    cases we previously fell back to MS for. We'll wire MS in proper in week 2
    once we have validation data to know whether it actually helps.

    Reference: https://github.com/microsoft/USBuildingFootprints
    """
    LOG.info("footprint_v2: MS Footprints lookup not yet wired (stub); skipping")
    return None


def get_building_footprint(lat: float, lon: float) -> Optional[BuildingFootprint]:
    """Public entry point. Returns None if no building footprint found.

    Order: OSM (via OSMnx) → Microsoft Footprints → None.
    Caller decides what to do with None (typically: fall back to Solar API).
    """
    fp = _osm_footprint(lat, lon)
    if fp is not None:
        LOG.info("footprint_v2: got footprint via OSM (%d vertices)", len(fp.polygon_lonlat))
        return fp
    fp = _ms_footprint(lat, lon)
    if fp is not None:
        LOG.info("footprint_v2: got footprint via Microsoft Footprints")
        return fp
    LOG.info("footprint_v2: no footprint found at (%.6f, %.6f)", lat, lon)
    return None


# Helper for downstream compatibility — old footprint.py exposed these functions.
# We re-export them here so `from .footprint_v2 import polygon_bbox` works the
# same way as `from .footprint import polygon_bbox`.

def polygon_bbox(polygon_lonlat: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    """Return (min_lon, min_lat, max_lon, max_lat)."""
    lons = [p[0] for p in polygon_lonlat]
    lats = [p[1] for p in polygon_lonlat]
    return min(lons), min(lats), max(lons), max(lats)


def polygon_area_m2(polygon_lonlat: List[Tuple[float, float]]) -> float:
    """Approximate polygon area in square meters using local-meters projection.

    Good enough for buildings; loses accuracy for very large polygons but those
    aren't houses anyway.
    """
    if len(polygon_lonlat) < 3:
        return 0.0
    centroid_lat = sum(p[1] for p in polygon_lonlat) / len(polygon_lonlat)
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(centroid_lat))
    centroid_lon = sum(p[0] for p in polygon_lonlat) / len(polygon_lonlat)
    # Shoelace in meters
    pts = [
        ((lon - centroid_lon) * m_per_deg_lon, (lat - centroid_lat) * m_per_deg_lat)
        for lon, lat in polygon_lonlat
    ]
    n = len(pts)
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += (x1 * y2) - (x2 * y1)
    return abs(s) / 2.0
