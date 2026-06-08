"""Google Solar API adapter.

The Google Solar API's `buildingInsights:findClosest` endpoint returns a
`BuildingInsights` object that includes everything we need for an EagleView-style
measurement: per-facet area, pitch, azimuth, total roof area, and imagery date.
This module wraps that endpoint and returns the same `RoofMeasurement` shape
the LiDAR engine returns, so it can be used as a primary OR fallback engine.

Endpoint reference:
  https://developers.google.com/maps/documentation/solar/reference/rest/v1/buildingInsights/findClosest

Pricing (as of writing): ~$0.50 per request after the free tier. Cheap enough
to use as a fallback for the ~20% of addresses where free LiDAR fails or is stale.

Key response fields we use:
  solarPotential.wholeRoofStats.areaMeters2          -> total roof surface area (3D)
  solarPotential.roofSegmentStats[]                  -> per-facet:
      .azimuthDegrees                                -> 0=N, 90=E, ...
      .pitchDegrees                                  -> degrees from horizontal
      .stats.areaMeters2                             -> facet area (3D)
      .stats.sunshineQuantiles                       -> ignored here
      .planeHeightAtCenterMeters                     -> z-height of facet center
      .center.latitude, .center.longitude            -> facet centroid (WGS84)
      .boundingBox                                   -> facet 2D bbox
  imageryDate                                        -> {year, month, day}
  imageryQuality                                     -> "HIGH" | "MEDIUM" | "LOW"
  imageryProcessedDate                               -> when the model ran
  postalCode, administrativeArea, regionCode         -> address echo-back
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
from roofmeasure.data_layers import add_polygons_to_facets
LOG = logging.getLogger(__name__)

SOLAR_API_BASE = "https://solar.googleapis.com/v1"
DEFAULT_TIMEOUT = 25.0  # seconds; Solar API is usually fast


class SolarApiError(RuntimeError):
    """Raised for non-recoverable Solar API errors (auth, quota, bad request)."""


class SolarApiNotFoundError(SolarApiError):
    """Raised when Solar API has no coverage for the requested point.

    This is the trigger for falling back to another engine (LiDAR, hash heuristic).
    """


@dataclass
class SolarResult:
    """Raw parsed Solar API response, normalized into our schema."""

    facets: List[Dict[str, Any]]              # [{areaSqFt, pitch, azimuthDeg, centroid, ...}]
    total_area_m2: float                       # whole-roof 3D area
    footprint_area_m2: Optional[float]         # building stats area (horizontal)
    imagery_date: Optional[str]                # ISO date string
    imagery_quality: str                       # HIGH | MEDIUM | LOW
    center_lat: float
    center_lon: float
    raw_payload: Dict[str, Any]                # full response, kept for audit


# ---------------------------------------------------------------------------
# Low-level HTTP
# ---------------------------------------------------------------------------

def fetch_building_insights(
    lat: float,
    lon: float,
    api_key: Optional[str] = None,
    required_quality: str = "HIGH",   # HIGH | MEDIUM | LOW
    timeout_s: float = DEFAULT_TIMEOUT,
) -> SolarResult:
    """Call Solar API's findClosest endpoint and normalize the response.

    Raises:
      SolarApiNotFoundError - no coverage for the point (404)
      SolarApiError         - all other failures (auth, quota, network)
    """
    api_key = api_key or os.environ.get("GOOGLE_SOLAR_API_KEY") or os.environ.get(
        "GOOGLE_MAPS_API_KEY"
    )
    if not api_key:
        raise SolarApiError(
            "no Google API key configured (set GOOGLE_SOLAR_API_KEY or GOOGLE_MAPS_API_KEY)"
        )
    url = f"{SOLAR_API_BASE}/buildingInsights:findClosest"
    params = {
        "location.latitude": lat,
        "location.longitude": lon,
        "requiredQuality": required_quality,
        "key": api_key,
    }
    try:
        r = requests.get(url, params=params, timeout=timeout_s)
    except requests.RequestException as exc:
        raise SolarApiError(f"network error: {exc}") from exc

    if r.status_code == 404:
        raise SolarApiNotFoundError(
            f"no Solar API coverage at ({lat:.6f}, {lon:.6f}) "
            f"(quality={required_quality}). Try a lower quality or a different engine."
        )
    if r.status_code == 403:
        raise SolarApiError(
            "Solar API returned 403. Verify the key is enabled for Solar API in your "
            "Google Cloud project and billing is active."
        )
    if r.status_code == 429:
        raise SolarApiError("Solar API quota exceeded (429). Slow down requests or raise quota.")
    if r.status_code != 200:
        raise SolarApiError(
            f"Solar API returned {r.status_code}: {r.text[:200]}"
        )

    try:
        payload = r.json()
    except ValueError as exc:
        raise SolarApiError(f"Solar API returned non-JSON: {exc}") from exc

    return parse_building_insights(payload)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

M2_TO_FT2 = 10.7639
M_TO_FT = 3.28084


def parse_building_insights(payload: Dict[str, Any]) -> SolarResult:
    """Convert the raw Solar API payload into our normalized SolarResult."""
    solar = payload.get("solarPotential") or {}
    whole = solar.get("wholeRoofStats") or {}
    building = solar.get("buildingStats") or {}
    segments = solar.get("roofSegmentStats") or []
    total_area_m2 = float(whole.get("areaMeters2") or 0.0)
    footprint_area_m2 = float(building.get("areaMeters2") or 0.0) or None

    center = payload.get("center") or {}
    center_lat = float(center.get("latitude") or 0.0)
    center_lon = float(center.get("longitude") or 0.0)

    imagery_date = None
    img = payload.get("imageryDate") or {}
    if img:
        try:
            imagery_date = f"{int(img['year']):04d}-{int(img['month']):02d}-{int(img['day']):02d}"
        except (KeyError, ValueError, TypeError):
            imagery_date = None

    imagery_quality = str(payload.get("imageryQuality") or "UNKNOWN")

    facets: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        stats = seg.get("stats") or {}
        area_m2 = float(stats.get("areaMeters2") or 0.0)
        pitch_deg = float(seg.get("pitchDegrees") or 0.0)
        azimuth_deg = float(seg.get("azimuthDegrees") or 0.0)
        height_m = float(seg.get("planeHeightAtCenterMeters") or 0.0)
        seg_center = seg.get("center") or {}
        pitch_x_in_12 = 12.0 * math.tan(math.radians(pitch_deg))
        facets.append({
            "id": idx,
            "areaSqFt": round(area_m2 * M2_TO_FT2, 1),
            "areaM2": round(area_m2, 2),
            "pitch": f"{round(pitch_x_in_12)}/12",
            "pitchExact": round(pitch_x_in_12, 2),
            "pitchDeg": round(pitch_deg, 2),
            "azimuthDeg": round(azimuth_deg, 1),
            "planeHeightMCenter": round(height_m, 2),
            "centerLat": float(seg_center.get("latitude") or 0.0),
            "centerLon": float(seg_center.get("longitude") or 0.0),
        })

    return SolarResult(
        facets=facets,
        total_area_m2=total_area_m2,
        footprint_area_m2=footprint_area_m2,
        imagery_date=imagery_date,
        imagery_quality=imagery_quality,
        center_lat=center_lat,
        center_lon=center_lon,
        raw_payload=payload,
    )


# ---------------------------------------------------------------------------
# Adapter to RoofMeasurement shape
# ---------------------------------------------------------------------------

def solar_result_to_measurement_dict(
    solar: SolarResult,
    address: str,
    price_low_per_sqft_cents: int = 450,
    price_high_per_sqft_cents: int = 850,
    minimum_project_cents: int = 250000,
    default_waste_pct: float = 12.0,
) -> Dict[str, Any]:
    """Return a dict shaped like our RoofMeasurement -> drop-in for the Next.js client."""
    total_area_m2 = solar.total_area_m2
    total_area_sqft = total_area_m2 * M2_TO_FT2
    footprint_m2 = solar.footprint_area_m2 or 0.0
    footprint_sqft = footprint_m2 * M2_TO_FT2

    # Predominant pitch: weighted-by-area mode, rounded
    if solar.facets:
        weighted = sum(f["pitchExact"] * f["areaM2"] for f in solar.facets)
        total_facet_area = sum(f["areaM2"] for f in solar.facets) or 1.0
        predominant_x = round(weighted / total_facet_area)
    else:
        predominant_x = 6
    predominant_pitch_str = f"{int(predominant_x)}/12"

    # Pitch breakdown
    pitch_groups: Dict[int, float] = {}
    for f in solar.facets:
        key = int(round(f["pitchExact"]))
        pitch_groups[key] = pitch_groups.get(key, 0.0) + f["areaSqFt"]
    pitch_areas = []
    if total_area_sqft > 0:
        for key, area_sqft in sorted(pitch_groups.items(), key=lambda kv: -kv[1]):
            pitch_areas.append({
                "pitch": f"{key}/12",
                "areaSqFt": round(area_sqft, 1),
                "percent": round(area_sqft / total_area_sqft * 100, 1),
            })

    quote_sqft = total_area_sqft * (1 + default_waste_pct / 100)
    cost_low = max(minimum_project_cents / 100, quote_sqft * price_low_per_sqft_cents / 100)
    cost_high = max(cost_low, quote_sqft * price_high_per_sqft_cents / 100)

    # Confidence from imagery quality
    quality_conf = {"HIGH": 92, "MEDIUM": 80, "LOW": 65}.get(solar.imagery_quality, 70)

    # Line measurements: Solar API does NOT give per-edge ridge/hip/valley breakdown.
    # We provide totals=0 and a note so the report layer can render gracefully.
    line_measurements = {
        "ridgesFt": 0.0, "hipsFt": 0.0, "valleysFt": 0.0,
        "eavesFt": 0.0, "rakesFt": 0.0,
        "ridgesHipsFt": 0.0, "dripEdgeFt": 0.0,
        "penetrations": 0.0, "penetrationsAreaSqFt": 0.0,
        "flashingFt": 0.0, "stepFlashingFt": 0.0,
    }

    notes = [
        f"Roof geometry from Google Solar API ({solar.imagery_quality} quality imagery).",
        f"Imagery captured: {solar.imagery_date or 'unknown date'}.",
        "Per-edge ridge/hip/valley breakdown not available from Solar API; "
        "use the LiDAR engine for those metrics or order a verified report.",
        "Total roof area is the 3D pitch-corrected surface returned by Solar API.",
    ]

    return {
        "roofAreaSqFt": round(total_area_sqft, 1),
        "footprintSqFt": round(footprint_sqft, 1),
        "roofingSquares": round(math.ceil(total_area_sqft / 100 * 3) / 3, 2),
        "suggestedWastePercent": default_waste_pct,
        "quoteReadySqFt": round(quote_sqft, 1),
        "quoteReadySquares": round(math.ceil(quote_sqft / 100 * 3) / 3, 2),
        "estimatedCostLow": round(cost_low, 0),
        "estimatedCostHigh": round(cost_high, 0),
        "pitchSummary": (
            f"Predominant pitch {predominant_pitch_str}; weighted average across "
            f"{len(solar.facets)} facets. Geometry from Google Solar API."
        ),
        "predominantPitch": predominant_pitch_str,
        "facetCount": len(solar.facets),
        "sourceSummary": (
            f"Google Solar API buildingInsights (imagery quality "
            f"{solar.imagery_quality}, captured {solar.imagery_date})."
        ),
        "locationSummary": {
            "lat": solar.center_lat, "lng": solar.center_lon,
            "countrySupport": "Google Solar API coverage area",
            "propertyClass": "residential",
        },
        "disclaimer": (
            "Automated roof measurement from Google Solar API. Verify on-site before "
            "ordering materials, filing insurance claims, or submitting permit drawings."
        ),
        "confidenceScore": quality_conf,
        "facets": solar.facets,
        "edges": [],
        "obstructions": [],
        "lineMeasurements": {k: round(v, 1) for k, v in line_measurements.items()},
        "pitchAreas": pitch_areas,
        "facetAreas": sorted(f["areaSqFt"] for f in solar.facets),
        "measurementNotes": notes,
        "dataSources": {
            "geocoder": "google",
            "footprint": "google_solar",
            "lidar": "google_solar",
            "imageryDate": solar.imagery_date,
            "imageryQuality": solar.imagery_quality,
        },
    }


# ---------------------------------------------------------------------------
# Convenience: top-level entry the orchestrator can call
# ---------------------------------------------------------------------------

def measure_via_solar_api(
    address: str,
    lat: float,
    lon: float,
    required_quality: str = "HIGH",
    api_key: Optional[str] = None,
    **pricing_kwargs,
) -> Dict[str, Any]:
    """High-level: lat/lon -> Solar API -> RoofMeasurement-shaped dict.

    Raises SolarApiNotFoundError if no coverage at the point.
    """
    api_key = api_key or os.environ.get("GOOGLE_SOLAR_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
    result = fetch_building_insights(lat, lon, api_key=api_key,
                                     required_quality=required_quality)
    try:
        add_polygons_to_facets(
            result.facets,
            result.center_lat or lat,
            result.center_lon or lon,
            api_key=api_key or "",
        )
    except Exception:
        LOG.exception("data_layers polygon extraction failed; continuing without polygons")
    return solar_result_to_measurement_dict(result, address, **pricing_kwargs)