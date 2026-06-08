"""Building footprint discovery — robust v2.

Improvements over week1-migration/footprint_v2.py:
  - Progressive radius expansion (80m → 150m → 300m) before giving up.
  - Direct Overpass fallback (with proper User-Agent) when OSMnx returns empty,
    in case it's a transient OSMnx-side issue.
  - Microsoft Canadian Building Footprints wired in for Canadian addresses.
  - Microsoft US Building Footprints wired in for US addresses.
  - Same drop-in BuildingFootprint dataclass shape.

Strategy (priority order):
  1. OSM via OSMnx (radius escalation)
  2. OSM via direct Overpass POST (radius escalation, second chance)
  3. Microsoft Building Footprints (country-routed)
  4. Return None — caller falls back to Solar API or surrenders.

OSMnx: https://github.com/gboeing/osmnx
Microsoft US Footprints: https://github.com/microsoft/USBuildingFootprints
Microsoft CA Footprints: https://github.com/microsoft/CanadianBuildingFootprints
Microsoft Global ML Footprints: https://github.com/microsoft/GlobalMLBuildingFootprints
"""
from __future__ import annotations

import io
import json
import logging
import math
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# OSM_SANITY_V312_INLINE
def _osm_polygon_looks_sane(polygon, lat=None, lon=None):
    try:
        from pyproj import Geod
        area_m2 = abs(Geod(ellps="WGS84").geometry_area_perimeter(polygon)[0])
    except Exception:
        return True
    if area_m2 < 50 or area_m2 > 2000:
        import logging
        logging.getLogger('roofmeasure').warning(f'OSM polygon {area_m2:.0f} m2 out of [50,2000] - rejecting')
        return False
    return True


try:
    import osmnx as ox
    import geopandas as gpd
    from shapely.geometry import Point, Polygon, shape, box as shapely_box
    HAS_OSMNX = True
except ImportError:
    HAS_OSMNX = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

LOG = logging.getLogger(__name__)

USER_AGENT = "RoofMeasureEngine/2.0 (https://roofmeasure.canadasroofer.com)"

# Radii to try, in order, when looking for a building containing/near the query point.
SEARCH_RADII_M = [80, 150, 300]

# Overpass mirrors — OSMnx already iterates these, but we keep a direct fallback.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]

# Microsoft Footprints cache
MS_FOOTPRINTS_CACHE = Path("/var/cache/roofmeasure/ms-footprints")
MS_FOOTPRINTS_CACHE.mkdir(parents=True, exist_ok=True)

# Microsoft Footprints data URLs (subject to change — these are the canonical CDN paths).
# CA file is a single combined GeoJSON ZIP (~1GB+). US files are per-state ZIPs.
MS_CA_URL = "https://usbuildingdata.blob.core.windows.net/canadian-buildings-v2/Ontario.geojson.zip"
MS_US_URL_PATTERN = "https://usbuildingdata.blob.core.windows.net/usbuildings-v2/{state}.geojson.zip"

# Rough lat/lon → US state mapping (just the ones we care about for now).
# This is a starter — we'll expand once we know which states have real users.
# For an actual production lookup, use the `us` package or a shapefile lookup.
US_STATE_BBOXES = {
    "Texas": (-106.65, 25.84, -93.51, 36.50),
    "California": (-124.48, 32.53, -114.13, 42.01),
    "Florida": (-87.63, 24.50, -80.03, 31.00),
    "NewYork": (-79.76, 40.49, -71.86, 45.02),
    "Georgia": (-85.61, 30.36, -80.84, 35.00),
    "NorthCarolina": (-84.32, 33.84, -75.46, 36.59),
    "Arizona": (-114.82, 31.33, -109.05, 37.00),
    "Illinois": (-91.51, 36.97, -87.50, 42.51),
    "Ohio": (-84.82, 38.40, -80.52, 41.98),
    "Pennsylvania": (-80.52, 39.72, -74.69, 42.27),
    "Michigan": (-90.42, 41.70, -82.41, 48.31),
    "Washington": (-124.85, 45.54, -116.92, 49.00),
    "Massachusetts": (-73.51, 41.24, -69.93, 42.89),
    "Virginia": (-83.68, 36.54, -75.24, 39.47),
    "Colorado": (-109.06, 36.99, -102.04, 41.00),
}


@dataclass
class BuildingFootprint:
    polygon_lonlat: List[Tuple[float, float]]
    source: str  # "osm" | "overpass_direct" | "microsoft_us" | "microsoft_ca"
    centroid_lonlat: Tuple[float, float]
    osm_id: Optional[str] = None


# ---------------------------------------------------------------------------
# OSMnx path — with radius escalation
# ---------------------------------------------------------------------------

def _osmnx_at_radius(lat: float, lon: float, radius_m: float) -> Optional[BuildingFootprint]:
    """Single OSMnx query at one radius. Returns None on no result or error."""
    if not HAS_OSMNX:
        return None
    try:
        ox.settings.user_agent = USER_AGENT
        ox.settings.requests_timeout = 30
        gdf = ox.features_from_point(
            (lat, lon),
            tags={"building": True},
            dist=radius_m,
        )
    except Exception as e:
        LOG.warning("footprint_v2: OSMnx at %dm failed: %s", radius_m, e)
        return None

    if gdf is None or gdf.empty:
        return None

    poly_rows = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if poly_rows.empty:
        return None

    fp_result = _pick_best_polygon(poly_rows, lat, lon, source="osm")
    if fp_result is not None:
        from shapely.geometry import Polygon as _Poly
        try:
            _poly = _Poly([(c[0], c[1]) for c in fp_result.polygon_lonlat])
            if not _osm_polygon_looks_sane(_poly):
                return None
        except Exception:
            pass
    return fp_result


def _osmnx_footprint(lat: float, lon: float) -> Optional[BuildingFootprint]:
    """OSMnx with progressive radius expansion."""
    for radius in SEARCH_RADII_M:
        LOG.info("footprint_v2: trying OSMnx at radius=%dm", radius)
        fp = _osmnx_at_radius(lat, lon, radius)
        if fp is not None:
            LOG.info("footprint_v2: OSMnx hit at radius=%dm (%d vertices)",
                     radius, len(fp.polygon_lonlat))
            return fp
    return None


# ---------------------------------------------------------------------------
# Direct Overpass fallback — explicit POST with User-Agent
# ---------------------------------------------------------------------------

def _overpass_footprint(lat: float, lon: float) -> Optional[BuildingFootprint]:
    """Direct Overpass POST with User-Agent header. Tries each mirror."""
    if not HAS_REQUESTS or not HAS_OSMNX:
        return None

    for radius in SEARCH_RADII_M:
        # Overpass QL: find way[building] within radius_m around (lat, lon)
        # `out geom;` returns the geometry inline so we don't need a second call.
        ql = (
            f"[out:json][timeout:30];"
            f'(way["building"](around:{radius},{lat},{lon});'
            f'relation["building"](around:{radius},{lat},{lon}););'
            f"out body geom;"
        )
        for mirror in OVERPASS_MIRRORS:
            try:
                r = requests.post(
                    mirror,
                    data={"data": ql},
                    timeout=30,
                    headers={"User-Agent": USER_AGENT},
                )
                if r.status_code != 200:
                    LOG.warning("footprint_v2: overpass %s status %d", mirror, r.status_code)
                    continue
                data = r.json()
            except Exception as e:
                LOG.warning("footprint_v2: overpass %s err: %s", mirror, e)
                continue

            elements = data.get("elements", []) or []
            if not elements:
                continue

            # Convert ways/relations into shapely polygons and find best
            from shapely.geometry import Polygon as SPoly
            candidates = []
            for el in elements:
                geom = el.get("geometry")
                if not geom:
                    continue
                coords = [(g["lon"], g["lat"]) for g in geom]
                if len(coords) < 3:
                    continue
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                try:
                    p = SPoly(coords)
                    if not p.is_valid:
                        p = p.buffer(0)
                    candidates.append((el, p))
                except Exception:
                    continue

            if not candidates:
                continue

            pt = Point(lon, lat)
            containing = [(el, p) for (el, p) in candidates if p.contains(pt)]
            chosen_el, chosen_geom = (
                containing[0] if containing
                else min(candidates, key=lambda ep: ep[1].distance(pt))
            )
            cent = chosen_geom.centroid
            LOG.info("footprint_v2: overpass direct hit at radius=%dm via %s",
                     radius, mirror)
            return BuildingFootprint(
                polygon_lonlat=list(chosen_geom.exterior.coords),
                source="overpass_direct",
                centroid_lonlat=(cent.x, cent.y),
                osm_id=str(chosen_el.get("id")),
            )

    return None


# ---------------------------------------------------------------------------
# Microsoft Building Footprints — Canada
# ---------------------------------------------------------------------------

def _is_canada(lat: float, lon: float) -> bool:
    """Very rough — Canada is roughly lat 41-83, lon -141 to -52."""
    return 41.0 <= lat <= 83.0 and -141.0 <= lon <= -52.0


def _is_ontario(lat: float, lon: float) -> bool:
    """Rough Ontario bbox."""
    return 41.6 <= lat <= 57.0 and -95.2 <= lon <= -74.3


def _ms_canada_footprint(lat: float, lon: float) -> Optional[BuildingFootprint]:
    """Microsoft Canadian Building Footprints.

    Only Ontario is wired in for now (where Bill is). Other provinces follow
    the same pattern — just swap the URL.

    Total Canadian dataset: ~12M buildings. Ontario alone is several million.
    File is ~hundreds of MB; we cache once.
    """
    if not _is_ontario(lat, lon):
        LOG.info("footprint_v2: MS-CA: not in Ontario bbox")
        return None
    return _ms_lookup(lat, lon, url=MS_CA_URL, label="Ontario", source="microsoft_ca")


def _ms_us_footprint(lat: float, lon: float) -> Optional[BuildingFootprint]:
    """Microsoft US Building Footprints — pick state by rough bbox."""
    for state, bbox in US_STATE_BBOXES.items():
        if bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]:
            url = MS_US_URL_PATTERN.format(state=state)
            return _ms_lookup(lat, lon, url=url, label=state, source="microsoft_us")
    LOG.info("footprint_v2: MS-US: no state matched (lat=%s, lon=%s)", lat, lon)
    return None


def _ms_lookup(
    lat: float, lon: float,
    *, url: str, label: str, source: str,
) -> Optional[BuildingFootprint]:
    """Download (cached) + spatial-search a Microsoft Footprints GeoJSON ZIP."""
    if not HAS_REQUESTS or not HAS_OSMNX:
        return None

    cache_zip = MS_FOOTPRINTS_CACHE / f"{label}.geojson.zip"
    cache_geojson = MS_FOOTPRINTS_CACHE / f"{label}.geojson"
    cache_parquet = MS_FOOTPRINTS_CACHE / f"{label}.parquet"

    # Fastest path: parquet (we convert once after the first download)
    if cache_parquet.exists():
        return _ms_query_parquet(cache_parquet, lat, lon, source)

    # Next: cached GeoJSON
    if cache_geojson.exists():
        gdf = _load_geojson_to_gdf(cache_geojson)
        # Convert to parquet for next time
        try:
            gdf.to_parquet(cache_parquet)
            LOG.info("footprint_v2: cached parquet at %s", cache_parquet)
        except Exception as e:
            LOG.warning("footprint_v2: parquet cache failed: %s", e)
        return _ms_query_gdf(gdf, lat, lon, source)

    # Last resort: download the ZIP
    if not cache_zip.exists():
        LOG.info("footprint_v2: downloading MS Footprints %s -> %s", url, cache_zip)
        try:
            with requests.get(url, stream=True, timeout=600,
                              headers={"User-Agent": USER_AGENT}) as r:
                r.raise_for_status()
                with open(cache_zip, "wb") as f:
                    for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                        if chunk:
                            f.write(chunk)
        except Exception as e:
            LOG.warning("footprint_v2: MS download failed: %s", e)
            return None

    # Unzip
    try:
        with zipfile.ZipFile(cache_zip) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".geojson")]
            if not names:
                LOG.warning("footprint_v2: no geojson in MS zip")
                return None
            with zf.open(names[0]) as src, open(cache_geojson, "wb") as dst:
                dst.write(src.read())
    except Exception as e:
        LOG.warning("footprint_v2: MS unzip failed: %s", e)
        return None

    gdf = _load_geojson_to_gdf(cache_geojson)
    try:
        gdf.to_parquet(cache_parquet)
    except Exception:
        pass
    return _ms_query_gdf(gdf, lat, lon, source)


def _load_geojson_to_gdf(path: Path):
    """Load a Microsoft Footprints GeoJSON into a GeoDataFrame.

    Microsoft files are line-delimited GeoJSON (one feature per line) for the
    newer datasets, and standard GeoJSON FeatureCollection for older ones.
    Try the line-delimited path first since it's much more memory-efficient.
    """
    if not HAS_OSMNX:
        raise RuntimeError("geopandas not installed")
    try:
        gdf = gpd.read_file(str(path))
        LOG.info("footprint_v2: loaded MS gdf with %d features from %s",
                 len(gdf), path.name)
        return gdf
    except Exception as e:
        LOG.warning("footprint_v2: gpd.read_file failed (%s); trying line-by-line", e)
        # Line-delimited fallback
        features = []
        with open(path) as f:
            for line in f:
                line = line.strip().rstrip(",")
                if not line or line in ("[", "]"):
                    continue
                try:
                    obj = json.loads(line)
                    if "geometry" in obj:
                        features.append(obj)
                except Exception:
                    continue
        gdf = gpd.GeoDataFrame.from_features(features)
        gdf.set_crs("EPSG:4326", inplace=True)
        LOG.info("footprint_v2: line-delim loaded MS gdf with %d features", len(gdf))
        return gdf


def _ms_query_gdf(gdf, lat: float, lon: float, source: str) -> Optional[BuildingFootprint]:
    """Spatial query on a fully-loaded GeoDataFrame.

    Uses geopandas' rtree spatial index (`.sindex`) so a query over millions
    of polygons stays O(log N) instead of O(N). The first .sindex access
    builds the tree; subsequent calls are instant.
    """
    from shapely.geometry import Point as SPoint, box as shp_box
    pt = SPoint(lon, lat)
    pad = 0.001  # ~110m at mid-latitudes
    window = shp_box(lon - pad, lat - pad, lon + pad, lat + pad)
    try:
        # rtree: find polygons whose bbox intersects our window
        possible_idx = list(gdf.sindex.query(window, predicate="intersects"))
    except Exception as e:
        LOG.warning("footprint_v2: sindex query failed (%s) — falling back to linear scan", e)
        nearby = gdf[gdf.geometry.intersects(window)]
    else:
        if not possible_idx:
            LOG.info("footprint_v2: sindex window empty")
            return None
        nearby = gdf.iloc[possible_idx]
        # Sindex returns by bbox only; do an actual-geometry intersects pass
        nearby = nearby[nearby.geometry.intersects(window)]

    if nearby.empty:
        LOG.info("footprint_v2: MS gdf has no features near query")
        return None
    return _pick_best_polygon(nearby, lat, lon, source=source)


def _ms_query_parquet(parquet_path: Path, lat: float, lon: float, source: str
                      ) -> Optional[BuildingFootprint]:
    """Query the cached parquet — much faster on repeat runs."""
    if not HAS_OSMNX:
        return None
    try:
        gdf = gpd.read_parquet(str(parquet_path))
    except Exception as e:
        LOG.warning("footprint_v2: parquet read failed: %s", e)
        return None
    return _ms_query_gdf(gdf, lat, lon, source)


# ---------------------------------------------------------------------------
# Pick best polygon from a GeoDataFrame
# ---------------------------------------------------------------------------



def _union_nearby_polygons(gdf, lat, lon, search_radius_m=5.0):
    from shapely.geometry import Point
    from shapely.ops import unary_union
    pt = Point(lon, lat)
    pad_deg = search_radius_m / 111_320.0
    nearby = gdf[gdf.geometry.distance(pt) <= pad_deg]
    if nearby.empty: return None
    union = unary_union(list(nearby.geometry))
    if hasattr(union, "geoms") and len(union.geoms) > 1:
        containing = [g for g in union.geoms if g.contains(pt)]
        primary = containing[0] if containing else min(union.geoms, key=lambda g: g.distance(pt))
        attached = [primary]
        for g in union.geoms:
            if g is primary: continue
            if g.distance(primary) <= (4.0 / 111_320.0): attached.append(g)
        union = unary_union(attached) if len(attached) > 1 else primary
    return union

def _pick_best_polygon(gdf, lat: float, lon: float, *, source: str) -> Optional[BuildingFootprint]:
    """Common selection logic: containing first, else closest. Returns the
    BuildingFootprint with the largest sub-polygon if MultiPolygon."""
    pt = Point(lon, lat)
    containing = gdf[gdf.geometry.contains(pt)]
    if not containing.empty:
        chosen = containing.iloc[0]
    else:
        candidates = gdf.copy()
        candidates["_dist"] = candidates.geometry.distance(pt)
        chosen = candidates.sort_values("_dist").iloc[0]

    try:
        merged = _union_nearby_polygons(gdf, lat, lon, search_radius_m=5.0)
        geom = merged if (merged is not None and not merged.is_empty) else chosen.geometry
    except Exception:
        geom = chosen.geometry
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    poly = list(geom.exterior.coords)

    osm_id = None
    try:
        osm_id = str(chosen.name)
    except Exception:
        pass
    cent = geom.centroid
    return BuildingFootprint(
        polygon_lonlat=poly,
        source=source,
        centroid_lonlat=(cent.x, cent.y),
        osm_id=osm_id,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _ms_global_ml_footprint(lat: float, lon: float) -> Optional[BuildingFootprint]:
    try:
        from .ms_global_ml import get_global_ml_polygon
        poly = get_global_ml_polygon(lat, lon)
        if poly is None:
            return None
        coords = list(poly.exterior.coords) if hasattr(poly, "exterior") else None
        if not coords:
            return None
        return BuildingFootprint(polygon_lonlat=coords, source="ms_global_ml")
    except Exception as e:
        LOG.warning(f"_ms_global_ml_footprint err: {e}")
        return None


def get_building_footprint(lat: float, lon: float) -> Optional[BuildingFootprint]:
    """Look up a building footprint by lat/lon. Multi-provider fallback chain.

    Order:
      1. OSMnx (radius escalation 80m → 150m → 300m)
      2. Direct Overpass POST (radius escalation, alternate mirror set)
      3. Microsoft Footprints — Canadian dataset (currently only Ontario)
      4. Microsoft Footprints — US dataset (state-routed)
      5. None
    """
    fp = _osmnx_footprint(lat, lon)
    if fp is not None:
        return fp

    fp = _overpass_footprint(lat, lon)
    if fp is not None:
        return fp

    fp = _ms_global_ml_footprint(lat, lon)
    if fp is not None:
        LOG.info("footprint_v2: MS Global ML hit at (%.6f, %.6f)", lat, lon)
        return fp

    if _is_canada(lat, lon):
        fp = _ms_canada_footprint(lat, lon)
    else:
        fp = _ms_us_footprint(lat, lon)
    if fp is not None:
        LOG.info("footprint_v2: MS hit at (%.6f, %.6f) source=%s",
                 lat, lon, fp.source)
        return fp

    LOG.info("footprint_v2: NO footprint at (%.6f, %.6f) after all providers", lat, lon)
    return None


# Helpers — kept verbatim from week 1 so callers can swap in cleanly
def polygon_bbox(polygon_lonlat: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    """Return (min_lon, min_lat, max_lon, max_lat)."""
    lons = [p[0] for p in polygon_lonlat]
    lats = [p[1] for p in polygon_lonlat]
    return min(lons), min(lats), max(lons), max(lats)


def polygon_area_m2(polygon_lonlat: List[Tuple[float, float]]) -> float:
    if len(polygon_lonlat) < 3:
        return 0.0
    centroid_lat = sum(p[1] for p in polygon_lonlat) / len(polygon_lonlat)
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(centroid_lat))
    centroid_lon = sum(p[0] for p in polygon_lonlat) / len(polygon_lonlat)
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
