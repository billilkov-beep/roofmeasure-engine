"""Address -> (lat, lon, normalized address) via free providers.

Provider chain: Google (if key) -> US Census (US only, free, no key) -> Nominatim (free, OSM).
All providers are wrapped in a single function `geocode_address` that returns a
GeocodeResult dataclass.

This module is intentionally dependency-light (requests + stdlib only) so it can
run on a Hostinger Node sidecar host without a heavy Python data-science stack.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

LOG = logging.getLogger(__name__)

USER_AGENT = os.environ.get(
    "ROOFMEASURE_USER_AGENT",
    "RoofMeasureEngine/0.1 (contact: support@canadasroofer.com)",
)


@dataclass
class GeocodeResult:
    lat: float
    lon: float
    matched_address: str
    source: str  # "google" | "census" | "nominatim"
    country: Optional[str] = None  # "US" | "CA" | other
    confidence: Optional[float] = None  # 0..1 if provider supplies it


class GeocodeError(RuntimeError):
    pass


def _google_geocode(address: str, api_key: str) -> Optional[GeocodeResult]:
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    r = requests.get(url, params={"address": address, "key": api_key}, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    loc = top["geometry"]["location"]
    country = None
    for comp in top.get("address_components", []):
        if "country" in comp.get("types", []):
            country = comp.get("short_name")
    return GeocodeResult(
        lat=float(loc["lat"]),
        lon=float(loc["lng"]),
        matched_address=top.get("formatted_address") or address,
        source="google",
        country=country,
    )


def _census_geocode(address: str) -> Optional[GeocodeResult]:
    """US-only. Free, no key, very accurate for US addresses."""
    url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    r = requests.get(
        url,
        params={
            "address": address,
            "benchmark": "Public_AR_Current",
            "format": "json",
        },
        timeout=10,
    )
    if r.status_code != 200:
        return None
    matches = (r.json().get("result") or {}).get("addressMatches") or []
    if not matches:
        return None
    top = matches[0]
    coords = top["coordinates"]
    return GeocodeResult(
        lat=float(coords["y"]),
        lon=float(coords["x"]),
        matched_address=top.get("matchedAddress") or address,
        source="census",
        country="US",
    )


def _nominatim_geocode(address: str) -> Optional[GeocodeResult]:
    url = "https://nominatim.openstreetmap.org/search"
    r = requests.get(
        url,
        params={
            "q": address,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            "countrycodes": "us,ca",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    data = r.json() or []
    if not data:
        return None
    top = data[0]
    addr = top.get("address") or {}
    cc = addr.get("country_code", "").upper() or None
    return GeocodeResult(
        lat=float(top["lat"]),
        lon=float(top["lon"]),
        matched_address=top.get("display_name") or address,
        source="nominatim",
        country=cc,
        confidence=float(top.get("importance") or 0) or None,
    )


def geocode_address(address: str) -> GeocodeResult:
    """Try providers in order, return the first success. Raises GeocodeError if all fail."""
    address = (address or "").strip()
    if not address:
        raise GeocodeError("empty address")

    api_key = os.environ.get("GOOGLE_GEOCODING_API_KEY") or os.environ.get(
        "GOOGLE_MAPS_API_KEY"
    )

    errors = []

    if api_key:
        try:
            res = _google_geocode(address, api_key)
            if res:
                return res
        except Exception as exc:  # pragma: no cover - network
            LOG.warning("Google geocode failed: %s", exc)
            errors.append(f"google:{exc}")

    # Census is free + US-only, try if the address looks US-ish or if we have no other data
    try:
        res = _census_geocode(address)
        if res:
            return res
    except Exception as exc:  # pragma: no cover - network
        LOG.warning("Census geocode failed: %s", exc)
        errors.append(f"census:{exc}")

    # Nominatim - be a good citizen, 1 rps
    try:
        time.sleep(0.2)
        res = _nominatim_geocode(address)
        if res:
            return res
    except Exception as exc:  # pragma: no cover - network
        LOG.warning("Nominatim geocode failed: %s", exc)
        errors.append(f"nominatim:{exc}")

    raise GeocodeError(f"all geocoders failed for {address!r}: {errors}")
