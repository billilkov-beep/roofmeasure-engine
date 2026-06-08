
"""NRCan HRDEM Canadian elevation via WCS 1.1.1."""
from __future__ import annotations
import logging, math, os
from io import BytesIO
from pathlib import Path
from typing import Optional
import numpy as np
try:
    import rasterio
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
from .footprint_v2 import BuildingFootprint, polygon_bbox
from .lidar_v2_raw import LidarCrop

LOG = logging.getLogger(__name__)
# Correct WCS endpoint (post-2024 redirect)
NRCAN_WCS_URL = "https://datacube.services.geo.ca/wrapper/ogc/elevation-hrdem-mosaic"
# WCS 1.1.1 coverage identifiers (verified via GetCapabilities)
NRCAN_COVERAGE_IDS = ["dsm", "dtm"]
CACHE_DIR = Path(os.environ.get("ROOFMEASURE_HRDEM_CACHE", "/var/cache/roofmeasure/nrcan_hrdem"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
USER_AGENT = "RoofMeasureEngine/3.13"
CANADA_BBOX = (-141.0, 41.0, -52.0, 84.0)


def _in_canada(lat, lon):
    return CANADA_BBOX[0] <= lon <= CANADA_BBOX[2] and CANADA_BBOX[1] <= lat <= CANADA_BBOX[3]


def _fetch_wcs(coverage_id, bbox, timeout=60.0):
    """WCS 1.1.1 GetCoverage. BoundingBox order: lat_min,lon_min,lat_max,lon_max for EPSG:4326."""
    if not HAS_REQUESTS:
        return None
    min_lon, min_lat, max_lon, max_lat = bbox
    # WCS 1.1.1 BoundingBox uses CRS-axis-order — for EPSG:4326 that's (lat, lon)
    bbox_str = f"{min_lat},{min_lon},{max_lat},{max_lon},urn:ogc:def:crs:EPSG::4326"
    params = {
        "service": "WCS",
        "version": "1.1.1",
        "request": "GetCoverage",
        "identifier": coverage_id,
        "BoundingBox": bbox_str,
        "format": "image/geotiff",
        "gridBaseCRS": "urn:ogc:def:crs:EPSG::4326",
    }
    try:
        r = requests.get(NRCAN_WCS_URL, params=params, timeout=timeout,
                         headers={"User-Agent": USER_AGENT})
    except Exception as e:
        LOG.info("nrcan: %s err %s", coverage_id, e)
        return None
    if r.status_code != 200:
        LOG.info("nrcan: %s HTTP %d (body: %s)", coverage_id, r.status_code, r.text[:200])
        return None
    ct = r.headers.get("Content-Type", "").lower()
    if "tif" not in ct and "octet" not in ct and "multipart" not in ct:
        LOG.info("nrcan: %s non-tiff ct=%s", coverage_id, ct)
        return None
    # WCS 1.1.1 may return multipart/related with the TIFF embedded
    if "multipart" in ct:
        # Extract the TIFF part from multipart body
        body = r.content
        # Find binary TIFF signature
        idx = body.find(b"II*\x00")  # little-endian TIFF magic
        if idx < 0:
            idx = body.find(b"MM\x00*")  # big-endian
        if idx < 0:
            LOG.info("nrcan: %s no TIFF in multipart", coverage_id)
            return None
        return body[idx:]
    return r.content


def _read_tiff(tiff_bytes, clon, clat):
    if not HAS_RASTERIO:
        return None
    try:
        with rasterio.open(BytesIO(tiff_bytes)) as src:
            arr = src.read(1).astype(np.float64)
            transform = src.transform
            crs = src.crs
            nodata = src.nodata
    except Exception as e:
        LOG.warning("nrcan: read failed: %s", e)
        return None
    h, w = arr.shape
    if h == 0 or w == 0:
        return None
    LOG.info("nrcan: GeoTIFF %dx%d crs=%s", h, w, crs)
    if nodata is not None:
        arr = np.where(arr == nodata, np.nan, arr)
    valid = np.isfinite(arr)
    if not valid.any():
        return None
    rows, cols = np.where(valid)
    xs, ys = rasterio.transform.xy(transform, rows.tolist(), cols.tolist())
    xs = np.asarray(xs); ys = np.asarray(ys); zs = arr[rows, cols]
    try:
        from pyproj import Transformer
        t = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        lons, lats = t.transform(xs, ys)
    except Exception as e:
        LOG.warning("nrcan: reproj err: %s", e)
        return None
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(clat))
    east = (lons - clon) * m_per_deg_lon
    north = (lats - clat) * m_per_deg_lat
    return np.column_stack([east, north, zs]).astype(np.float64)


def fetch_lidar_for_footprint(footprint, pad_m=6.0, prefer_dsm=True):
    if not HAS_RASTERIO or not HAS_REQUESTS:
        return None
    bbox = polygon_bbox(footprint.polygon_lonlat)
    min_lon, min_lat, max_lon, max_lat = bbox
    clat = (min_lat + max_lat) / 2
    clon = (min_lon + max_lon) / 2
    if not _in_canada(clat, clon):
        LOG.info("nrcan: not in Canada")
        return None
    pad_lat = pad_m / 111_320.0
    pad_lon = pad_m / (111_320.0 * math.cos(math.radians(clat)))
    bbox_pad = (min_lon - pad_lon, min_lat - pad_lat, max_lon + pad_lon, max_lat + pad_lat)
    order = NRCAN_COVERAGE_IDS if prefer_dsm else ["dtm", "dsm"]
    for cov in order:
        LOG.info("nrcan: trying %s", cov)
        tiff = _fetch_wcs(cov, bbox_pad)
        if tiff is None:
            continue
        pts = _read_tiff(tiff, clon, clat)
        if pts is None or len(pts) < 10:
            LOG.info("nrcan: %s too few points", cov)
            continue
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(clat))
        area_m2 = (bbox_pad[2] - bbox_pad[0]) * m_per_deg_lon * (bbox_pad[3] - bbox_pad[1]) * m_per_deg_lat
        density = len(pts) / max(1.0, area_m2)
        LOG.info("nrcan: %s OK %d pts (%.1f/m^2)", cov, len(pts), density)
        return LidarCrop(
            points_local_m=pts, crs_origin_lonlat=(clon, clat),
            source=f"nrcan_hrdem_{cov}", source_tile=cov,
            point_density_per_m2=float(density), captured_year=None,
            z_unit_detected="metre", classifications_used=False, raw_point_count=len(pts))
    LOG.info("nrcan: no coverage for (%.4f, %.4f)", clat, clon)
    return None
