"""Fetch Google Street View and Maps Static imagery for a property.

Used by the report layer to show the customer:
  1. A real photo of the front of their house (Street View)
  2. A real aerial / satellite view (Maps Static)

Both endpoints require GOOGLE_MAPS_API_KEY. The same key works for both APIs
once you enable "Street View Static API" and "Maps Static API" in your
Google Cloud project.

Pricing (as of 2025):
  - Street View Static: $7 per 1000 requests
  - Maps Static (satellite): $2 per 1000 requests
  - Together: ~$0.01 per property report.

Both responses are PNG. We cache them on disk so we don't pay twice for the
same address. Cache directory: ROOFMEASURE_CACHE_DIR/imagery/.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Optional, Tuple

import requests

LOG = logging.getLogger(__name__)

STREET_VIEW_BASE = "https://maps.googleapis.com/maps/api/streetview"
STREET_VIEW_METADATA = "https://maps.googleapis.com/maps/api/streetview/metadata"
MAPS_STATIC_BASE = "https://maps.googleapis.com/maps/api/staticmap"

DEFAULT_STREET_VIEW_SIZE = "640x400"     # 16:10 aspect, fits the cover-page hero
DEFAULT_AERIAL_SIZE = "640x640"          # square aerial fits the satellite page
DEFAULT_AERIAL_ZOOM = 20                 # building-level detail
DEFAULT_AERIAL_MAPTYPE = "satellite"     # or "hybrid" to overlay street names


@dataclass
class PropertyImagery:
    """Bundle of property images returned for the report."""
    street_view_path: Optional[str] = None
    street_view_url: Optional[str] = None
    street_view_heading: Optional[float] = None
    street_view_available: bool = False
    aerial_path: Optional[str] = None
    aerial_url: Optional[str] = None
    aerial_zoom: Optional[int] = None
    note: str = ""


def _cache_dir() -> str:
    base = os.environ.get("ROOFMEASURE_CACHE_DIR") or os.path.join(tempfile.gettempdir(), "roofmeasure_cache")
    d = os.path.join(base, "imagery")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_key(*parts) -> str:
    s = "|".join(str(p) for p in parts)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:20]


def _api_key() -> Optional[str]:
    return (os.environ.get("GOOGLE_MAPS_API_KEY")
            or os.environ.get("GOOGLE_GEOCODING_API_KEY")
            or None)


# ---------------------------------------------------------------------------
# Street View
# ---------------------------------------------------------------------------

def find_best_street_view_heading(lat: float, lon: float, api_key: Optional[str] = None
                                  ) -> Tuple[float, bool]:
    """Use Street View metadata to find the closest panorama + the heading that
    points roughly toward (lat, lon) — so the photo faces the house.

    Returns (heading_degrees, panorama_available).
    """
    api_key = api_key or _api_key()
    if not api_key:
        return 0.0, False
    try:
        r = requests.get(STREET_VIEW_METADATA, params={
            "location": f"{lat},{lon}",
            "key": api_key,
            "source": "outdoor",
        }, timeout=10)
        data = r.json()
        if data.get("status") != "OK":
            LOG.info("Street View not available at (%.6f, %.6f): %s",
                     lat, lon, data.get("status"))
            return 0.0, False
        # Compute heading from the panorama's pano location toward the building
        pano = data.get("location") or {}
        plat = float(pano.get("lat") or lat)
        plon = float(pano.get("lng") or lon)
        # Bearing from pano -> target
        import math
        dlon = math.radians(lon - plon)
        plat_r = math.radians(plat)
        tlat_r = math.radians(lat)
        x = math.sin(dlon) * math.cos(tlat_r)
        y = (math.cos(plat_r) * math.sin(tlat_r) -
             math.sin(plat_r) * math.cos(tlat_r) * math.cos(dlon))
        bearing = (math.degrees(math.atan2(x, y)) + 360.0) % 360.0
        return bearing, True
    except Exception as exc:
        LOG.warning("Street View metadata fetch failed: %s", exc)
        return 0.0, False


def fetch_street_view(lat: float, lon: float, *,
                      size: str = DEFAULT_STREET_VIEW_SIZE,
                      pitch: int = 0,
                      fov: int = 80,
                      api_key: Optional[str] = None,
                      heading: Optional[float] = None
                      ) -> Optional[Tuple[str, str, float]]:
    """Fetch the front-of-house Street View photo as PNG. Return (path, url, heading).
    Returns None when no panorama exists or the API key is missing.
    """
    api_key = api_key or _api_key()
    if not api_key:
        LOG.info("Street View skipped: no API key configured")
        return None

    if heading is None:
        heading, available = find_best_street_view_heading(lat, lon, api_key)
        if not available:
            return None

    path = os.path.join(_cache_dir(), f"streetview_{_cache_key(lat, lon, heading, size, fov)}.png")
    url = (f"{STREET_VIEW_BASE}?location={lat},{lon}&size={size}&heading={heading}"
           f"&pitch={pitch}&fov={fov}&source=outdoor&key={api_key}")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, url, heading

    try:
        r = requests.get(STREET_VIEW_BASE, params={
            "location": f"{lat},{lon}",
            "size": size,
            "heading": heading,
            "pitch": pitch,
            "fov": fov,
            "source": "outdoor",
            "key": api_key,
        }, timeout=15)
        if r.status_code != 200:
            LOG.warning("Street View HTTP %s: %s", r.status_code, r.text[:120])
            return None
        # Google returns a small "no imagery" placeholder when there's no real
        # photo. Detect by content length (< 5KB is the placeholder).
        if len(r.content) < 5_000:
            LOG.info("Street View returned placeholder (no real imagery at point)")
            return None
        with open(path, "wb") as f:
            f.write(r.content)
        return path, url, heading
    except Exception as exc:
        LOG.warning("Street View fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Aerial (Maps Static, satellite type)
# ---------------------------------------------------------------------------

def fetch_aerial(lat: float, lon: float, *,
                 size: str = DEFAULT_AERIAL_SIZE,
                 zoom: int = DEFAULT_AERIAL_ZOOM,
                 maptype: str = DEFAULT_AERIAL_MAPTYPE,
                 api_key: Optional[str] = None,
                 marker: bool = True
                 ) -> Optional[Tuple[str, str]]:
    """Fetch an aerial / satellite image of the property. Return (path, url)."""
    api_key = api_key or _api_key()
    if not api_key:
        LOG.info("Aerial skipped: no API key configured")
        return None

    path = os.path.join(_cache_dir(), f"aerial_{_cache_key(lat, lon, zoom, size, maptype)}.png")
    params = {
        "center": f"{lat},{lon}",
        "zoom": zoom,
        "size": size,
        "maptype": maptype,
        "scale": 2,            # retina-quality for crisp PDFs
        "key": api_key,
    }
    if marker:
        params["markers"] = f"color:red|size:mid|{lat},{lon}"
    # Build URL string for caching / report metadata
    from urllib.parse import urlencode
    url = f"{MAPS_STATIC_BASE}?{urlencode(params)}"

    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, url

    try:
        r = requests.get(MAPS_STATIC_BASE, params=params, timeout=15)
        if r.status_code != 200:
            LOG.warning("Maps Static HTTP %s: %s", r.status_code, r.text[:120])
            return None
        with open(path, "wb") as f:
            f.write(r.content)
        return path, url
    except Exception as exc:
        LOG.warning("Maps Static fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Top-level convenience
# ---------------------------------------------------------------------------

def fetch_property_imagery(lat: float, lon: float, *, api_key: Optional[str] = None
                           ) -> PropertyImagery:
    """Fetch both Street View + aerial for a property. Always returns a
    PropertyImagery; missing pieces are None."""
    api_key = api_key or _api_key()
    if not api_key:
        return PropertyImagery(note="GOOGLE_MAPS_API_KEY not set; imagery skipped")

    out = PropertyImagery()

    sv = fetch_street_view(lat, lon, api_key=api_key)
    if sv is not None:
        path, url, heading = sv
        out.street_view_path = path
        out.street_view_url = url
        out.street_view_heading = heading
        out.street_view_available = True
    else:
        out.note += "no Street View imagery at this point; "

    aerial = fetch_aerial(lat, lon, api_key=api_key)
    if aerial is not None:
        out.aerial_path, out.aerial_url = aerial
        out.aerial_zoom = DEFAULT_AERIAL_ZOOM
    else:
        out.note += "aerial fetch failed; "

    return out
