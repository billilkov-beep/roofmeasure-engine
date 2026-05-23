"""Building footprint extraction from free sources.

Strategy:
  1. Query Overpass (OpenStreetMap) for buildings near the geocoded point.
     OSM building polygons are dense in urban US/CA areas and reasonably good.
  2. If OSM has no building polygon at the point, fall back to a Microsoft
     Building Footprints lookup (US + Canada coverage, ML-derived, free).

For the Microsoft path we use the per-quadkey GeoJSON releases hosted at
https://minedbuildings.z5.web.core.windows.net (US) and the Canadian release
https://github.com/microsoft/CanadianBuildingFootprints. In production you'd
mirror those to your own bucket; for the prototype we hit them on demand and
cache locally.

Output is a polygon as a list of (lon, lat) vertices in WGS-84, plus simple
metrics computed in a local equal-area projection.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import requests

LOG = logging.getLogger(__name__)

Coord = Tuple[float, float]  # (lon, lat)


@dataclass
class BuildingFootprint:
    polygon_lonlat: List[Coord]  # exterior ring, closed (last == first)
    source: str  # "osm" | "microsoft"
    osm_id: Optional[int] = None
    centroid_lonlat: Optional[Coord] = None
    footprint_area_m2: Optional[float] = None
    properties: dict = field(default_factory=dict)


# ---------- Geometry helpers (pure stdlib, no shapely) ----------

def _point_in_polygon(pt: Coord, polygon: List[Coord]) -> bool:
    """Ray-casting point-in-polygon test on (lon, lat) coordinates."""
    x, y = pt
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _local_meters(lon: float, lat: float, lon0: float, lat0: float) -> Tuple[float, float]:
    """Equirectangular projection around (lon0, lat0). Good enough for a single building."""
    R = 6371000.0
    x = math.radians(lon - lon0) * math.cos(math.radians(lat0)) * R
    y = math.radians(lat - lat0) * R
    return x, y


def polygon_area_m2(polygon_lonlat: List[Coord]) -> float:
    if len(polygon_lonlat) < 3:
        return 0.0
    lon0 = sum(p[0] for p in polygon_lonlat) / len(polygon_lonlat)
    lat0 = sum(p[1] for p in polygon_lonlat) / len(polygon_lonlat)
    xs, ys = zip(*(_local_meters(lon, lat, lon0, lat0) for lon, lat in polygon_lonlat))
    s = 0.0
    n = len(xs)
    for i in range(n):
        j = (i + 1) % n
        s += xs[i] * ys[j] - xs[j] * ys[i]
    return abs(s) * 0.5


def polygon_centroid(polygon_lonlat: List[Coord]) -> Coord:
    n = len(polygon_lonlat)
    cx = sum(p[0] for p in polygon_lonlat) / n
    cy = sum(p[1] for p in polygon_lonlat) / n
    return cx, cy


def polygon_bbox(polygon_lonlat: List[Coord]) -> Tuple[float, float, float, float]:
    """Return (min_lon, min_lat, max_lon, max_lat)."""
    lons = [p[0] for p in polygon_lonlat]
    lats = [p[1] for p in polygon_lonlat]
    return min(lons), min(lats), max(lons), max(lats)


# ---------- OSM via Overpass ----------

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]


def _query_overpass(lat: float, lon: float, radius_m: int = 60) -> Optional[dict]:
    query = (
        f"[out:json][timeout:25];"
        f"(way(around:{radius_m},{lat},{lon})[\"building\"];"
        f" relation(around:{radius_m},{lat},{lon})[\"building\"];);"
        f"out geom;"
    )
    last_err = None
    for url in OVERPASS_ENDPOINTS:
        try:
            r = requests.post(url, data={"data": query}, timeout=30)
            if r.status_code == 200:
                return r.json()
            last_err = f"{url} -> {r.status_code}"
        except Exception as exc:  # pragma: no cover
            last_err = f"{url} -> {exc}"
    LOG.warning("Overpass failed on all endpoints: %s", last_err)
    return None


def _osm_footprint(lat: float, lon: float) -> Optional[BuildingFootprint]:
    data = _query_overpass(lat, lon)
    if not data:
        return None
    elements = data.get("elements") or []
    candidates = []
    for el in elements:
        if el.get("type") != "way":
            continue
        geom = el.get("geometry")
        if not geom or len(geom) < 4:
            continue
        ring = [(g["lon"], g["lat"]) for g in geom]
        # close it
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if _point_in_polygon((lon, lat), ring):
            # query point lands inside the polygon -> very likely target building
            candidates.append((0.0, el, ring))  # priority 0
        else:
            # otherwise rank by distance from polygon centroid to query point
            cx, cy = polygon_centroid(ring)
            d = (cx - lon) ** 2 + (cy - lat) ** 2
            candidates.append((1.0 + d, el, ring))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    _, el, ring = candidates[0]
    area = polygon_area_m2(ring)
    return BuildingFootprint(
        polygon_lonlat=ring,
        source="osm",
        osm_id=el.get("id"),
        centroid_lonlat=polygon_centroid(ring),
        footprint_area_m2=area,
        properties=el.get("tags") or {},
    )


# ---------- Microsoft Building Footprints fallback ----------
# In production: mirror the per-state / per-province GeoJSON releases to your own
# storage. For the prototype, we expect a local mirror path via env var.

def _microsoft_footprint(lat: float, lon: float) -> Optional[BuildingFootprint]:
    mirror = os.environ.get("MSBF_LOCAL_MIRROR")
    if not mirror or not os.path.isdir(mirror):
        return None
    # Each file is line-delimited GeoJSON. We assume the caller has
    # pre-filtered by state/province; this is a simple scan.
    target = (lon, lat)
    best = None
    best_area = float("inf")
    for name in os.listdir(mirror):
        if not name.endswith(".geojsonl") and not name.endswith(".geojson"):
            continue
        path = os.path.join(mirror, name)
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                try:
                    feat = json.loads(line)
                except json.JSONDecodeError:
                    continue
                geom = feat.get("geometry") or {}
                if geom.get("type") != "Polygon":
                    continue
                ring = [(c[0], c[1]) for c in (geom.get("coordinates") or [[]])[0]]
                if len(ring) < 4 or not _point_in_polygon(target, ring):
                    continue
                area = polygon_area_m2(ring)
                if area < best_area:
                    best_area = area
                    best = ring
    if not best:
        return None
    return BuildingFootprint(
        polygon_lonlat=best,
        source="microsoft",
        centroid_lonlat=polygon_centroid(best),
        footprint_area_m2=best_area,
    )


def get_building_footprint(lat: float, lon: float) -> BuildingFootprint:
    """Return the building polygon that contains (or is closest to) (lat, lon)."""
    osm = _osm_footprint(lat, lon)
    if osm:
        return osm
    ms = _microsoft_footprint(lat, lon)
    if ms:
        return ms
    raise RuntimeError(
        f"no building footprint found at ({lat:.6f}, {lon:.6f}). "
        "OSM had no nearby building and no Microsoft mirror is configured."
    )
