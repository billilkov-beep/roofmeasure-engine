"""Top-level orchestrator: address -> EagleView-style measurement dict.

Supports two engines:
  - Free LiDAR pipeline (USGS 3DEP / OpenTopography + RANSAC segmentation)
  - Google Solar API adapter (paid, $0.50/req, returns facet-level data)

Strategy via ROOFMEASURE_STRATEGY env or `strategy` kwarg:
  - "auto" (default): LiDAR first, Solar API fallback
  - "lidar_only" / "solar_only": single engine, no fallback
  - "solar_first": Solar API first (faster), LiDAR fallback
"""
from __future__ import annotations
import json
import logging
import math
import os
from dataclasses import fields as dataclass_fields, asdict, dataclass, field
from typing import Any, Dict, List, Optional
import numpy as np

from .footprint import BuildingFootprint, get_building_footprint, polygon_area_m2, _local_meters
from .geocode import GeocodeResult, geocode_address
from .lidar import LidarCrop, fetch_lidar_for_footprint, synthesize_test_pointcloud
from .obstructions import Obstruction, detect_obstructions_from_residuals
from .segmentation import RoofSegmentation, segment_roof
from .usage import time_call

LOG = logging.getLogger(__name__)

M2_TO_FT2 = 10.7639
M_TO_FT = 3.28084

STRATEGY_AUTO = "auto"
STRATEGY_LIDAR_ONLY = "lidar_only"
STRATEGY_SOLAR_ONLY = "solar_only"
STRATEGY_SOLAR_FIRST = "solar_first"


def _round_up_to_third_square(area_sqft):
    return math.ceil(area_sqft / 100 * 3) / 3


def _classify_predominant_pitch(facet_pitches_x_in_12_with_area):
    if not facet_pitches_x_in_12_with_area:
        return 6.0
    total_area = sum(a for _, a in facet_pitches_x_in_12_with_area)
    if total_area <= 0:
        return 6.0
    weighted_sum = sum(p * a for p, a in facet_pitches_x_in_12_with_area)
    return round(weighted_sum / total_area)


@dataclass
class RoofMeasurement:
    roofAreaSqFt: float
    footprintSqFt: float
    roofingSquares: float
    suggestedWastePercent: float
    quoteReadySqFt: float
    quoteReadySquares: float
    estimatedCostLow: float
    estimatedCostHigh: float
    pitchSummary: str
    predominantPitch: str
    facetCount: int
    sourceSummary: str
    locationSummary: Dict[str, Any]
    disclaimer: str
    confidenceScore: float
    facets: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    obstructions: List[Dict[str, Any]] = field(default_factory=list)
    lineMeasurements: Dict[str, float] = field(default_factory=dict)
    pitchAreas: List[Dict[str, Any]] = field(default_factory=list)
    facetAreas: List[float] = field(default_factory=list)
    measurementNotes: List[str] = field(default_factory=list)
    dataSources: Dict[str, Any] = field(default_factory=dict)
    accessoryTakeoff: List[Dict[str, Any]] = field(default_factory=list)
    obstructionSummary: Dict[str, int] = field(default_factory=dict)
    imagery: Dict[str, Any] = field(default_factory=dict)


    # Real roof/vector geometry for EagleView-style diagrams
    diagramGeometry: dict | None = None
    diagramGeometryStatus: str | None = None
    requiresReview: bool = False
    edges: list | None = None
    structureBreakdown: list | None = None

    def to_json(self):
        return json.dumps(asdict(self), indent=2, default=str)




def _coerce_roof_measurement(data: dict):
    allowed = {f.name for f in dataclass_fields(RoofMeasurement)}
    clean = {k: v for k, v in data.items() if k in allowed}
    return RoofMeasurement(**clean)


def _make_roof_measurement_safe(data: Dict[str, Any]) -> RoofMeasurement:
    allowed = {f.name for f in dataclass_fields(RoofMeasurement)}
    clean = {k: v for k, v in data.items() if k in allowed}
    return RoofMeasurement(**clean)

def measure_roof(address, *, price_low_per_sqft_cents=450, price_high_per_sqft_cents=850,
                 minimum_project_cents=250000, default_waste_pct=12.0,
                 use_synthetic_lidar=False, strategy=None):
    if not strategy:
        from .runtime_config import get_strategy
        strategy = get_strategy()
    pricing = dict(price_low=price_low_per_sqft_cents,
                   price_high=price_high_per_sqft_cents,
                   min_cents=minimum_project_cents,
                   waste_pct=default_waste_pct)

    with time_call(address, strategy) as record:
        try:
            gc = geocode_address(address)
            LOG.info("geocoded %r -> (%.6f, %.6f) via %s", address, gc.lat, gc.lon, gc.source)

            if strategy == STRATEGY_SOLAR_ONLY:
                result = _measure_via_solar(gc, address, **pricing)
                record(engine="solar", success=True,
                       roof_area_sqft=result.roofAreaSqFt,
                       confidence=result.confidenceScore)
                return result

            if strategy == STRATEGY_SOLAR_FIRST:
                try:
                    result = _measure_via_solar(gc, address, **pricing)
                    record(engine="solar", success=True,
                           roof_area_sqft=result.roofAreaSqFt,
                           confidence=result.confidenceScore)
                    return result
                except Exception as exc:
                    LOG.warning("solar engine failed (%s); falling back to LiDAR", exc)

            try:
                result = _measure_via_lidar(gc, address, use_synthetic_lidar, **pricing)
                record(engine="lidar", success=True,
                       roof_area_sqft=result.roofAreaSqFt,
                       confidence=result.confidenceScore)
                return result
            except Exception as exc:
                if strategy == STRATEGY_LIDAR_ONLY:
                    record(engine="lidar", success=False, error=str(exc))
                    raise
                LOG.warning("LiDAR engine failed (%s); falling back to Google Solar API", exc)
                result = _measure_via_solar(gc, address, **pricing)
                record(engine="solar", success=True,
                       roof_area_sqft=result.roofAreaSqFt,
                       confidence=result.confidenceScore)
                return result
        except Exception as exc:
            # Only record if we haven't already
            record(success=False, error=str(exc))
            raise


def _measure_via_lidar(gc, address, use_synthetic, price_low, price_high, min_cents, waste_pct):
    fp = get_building_footprint(gc.lat, gc.lon)
    fp_area_m2 = fp.footprint_area_m2 or polygon_area_m2(fp.polygon_lonlat)
    cx, cy = fp.centroid_lonlat or (gc.lon, gc.lat)
    LOG.info("footprint via %s: %.1f m^2 (%d-vertex polygon)",
             fp.source, fp_area_m2, len(fp.polygon_lonlat))
    if use_synthetic:
        cloud = _synthetic_cloud_for_footprint(fp, cx, cy)
        crop = LidarCrop(points_local_m=cloud, crs_origin_lonlat=(cx, cy), source="synthetic")
    else:
        crop = fetch_lidar_for_footprint(fp)
    LOG.info("lidar source=%s, %d points", crop.source, len(crop.points_local_m))
    seg = segment_roof(crop.points_local_m)
    LOG.info("segmentation: %d facets, %d edges", len(seg.facets), len(seg.edges))
    obs = detect_obstructions_from_residuals(crop.points_local_m, seg.facets)
    return _build_measurement(gc, fp, fp_area_m2, crop, seg, obs,
                              price_low, price_high, min_cents, waste_pct)


def _measure_via_solar(gc, address, price_low, price_high, min_cents, waste_pct):
    from .providers.google_solar import measure_via_solar_api
    LOG.info("calling Google Solar API for (%.6f, %.6f)", gc.lat, gc.lon)
    last_error = None
    data = None

    # Real production fallback: try Solar API at HIGH, then MEDIUM, then LOW.
    # Some USA addresses fail at HIGH even when Google has usable lower-quality data.
    for quality in ("HIGH", "MEDIUM", "LOW"):
        try:
            data = measure_via_solar_api(
                address, gc.lat, gc.lon,
                required_quality=quality,
                price_low_per_sqft_cents=price_low,
                price_high_per_sqft_cents=price_high,
                minimum_project_cents=min_cents,
                default_waste_pct=waste_pct,
            )
            data.setdefault("measurementNotes", []).append(f"Google Solar quality fallback used: {quality}")
            break
        except Exception as exc:
            last_error = exc
            LOG.warning("Google Solar failed at quality=%s: %s", quality, exc)

    if data is None:
        raise RuntimeError(f"Google Solar failed at HIGH/MEDIUM/LOW: {last_error}")
    data["dataSources"]["geocoder"] = gc.source
    # Attach imagery fetched the same way as LiDAR path
    from .imagery import fetch_property_imagery
    imagery = fetch_property_imagery(gc.lat, gc.lon)
    data["imagery"] = {
        "streetViewUrl": imagery.street_view_url,
        "streetViewPath": imagery.street_view_path,
        "streetViewHeading": imagery.street_view_heading,
        "streetViewAvailable": imagery.street_view_available,
        "aerialUrl": imagery.aerial_url,
        "aerialPath": imagery.aerial_path,
        "aerialZoom": imagery.aerial_zoom,
        "note": imagery.note,
    }
    return _coerce_roof_measurement(data)


def _build_measurement(gc, fp, fp_area_m2, crop, seg, obs,
                       price_low, price_high, min_cents, waste_pct):
    # Fetch property imagery once (Street View + aerial). Returns empty when
    # GOOGLE_MAPS_API_KEY isn't set, no error.
    from .imagery import fetch_property_imagery
    imagery = fetch_property_imagery(gc.lat, gc.lon)
    roof_area_m2 = sum(f.area_m2 for f in seg.facets)
    roof_area_sqft = roof_area_m2 * M2_TO_FT2
    footprint_sqft = fp_area_m2 * M2_TO_FT2
    pitches_with_area = [(f.pitch_x_in_12, f.area_m2) for f in seg.facets]
    predominant_x = _classify_predominant_pitch(pitches_with_area)
    predominant_pitch_str = "%d/12" % int(predominant_x)
    facet_areas_sqft = sorted(f.area_m2 * M2_TO_FT2 for f in seg.facets)
    pitch_groups = {}
    for f in seg.facets:
        key = int(round(f.pitch_x_in_12))
        pitch_groups[key] = pitch_groups.get(key, 0.0) + f.area_m2 * M2_TO_FT2
    pitch_areas = []
    if roof_area_sqft > 0:
        for key, area_sqft in sorted(pitch_groups.items(), key=lambda kv: -kv[1]):
            pitch_areas.append({
                "pitch": "%d/12" % key,
                "areaSqFt": round(area_sqft, 1),
                "percent": round(area_sqft / roof_area_sqft * 100, 1),
            })
    # Build the raw edge totals from the segmentation
    raw_edges = {"ridgesFt": 0.0, "hipsFt": 0.0, "valleysFt": 0.0,
                 "eavesFt": 0.0, "rakesFt": 0.0}
    for e in seg.edges:
        if e.kind == "ridge": raw_edges["ridgesFt"] += e.length_m * M_TO_FT
        elif e.kind == "hip": raw_edges["hipsFt"] += e.length_m * M_TO_FT
        elif e.kind == "valley": raw_edges["valleysFt"] += e.length_m * M_TO_FT
        elif e.kind == "eave": raw_edges["eavesFt"] += e.length_m * M_TO_FT
        elif e.kind == "rake": raw_edges["rakesFt"] += e.length_m * M_TO_FT
    raw_edges["penetrations"] = float(len(obs))
    raw_edges["penetrationsAreaSqFt"] = sum(o.estimated_area_m2 * M2_TO_FT2 for o in obs)
    # Hand off to the accessories module to derive rakes/flashing/step flashing/etc.
    from .accessories import estimate_accessories
    accessory_input = {
        "roofAreaSqFt": roof_area_sqft,
        "footprintSqFt": footprint_sqft,
        "suggestedWastePercent": waste_pct,
        "lineMeasurements": raw_edges,
        "facets": [{"id": f.id, "outline_xy": (None if f.outline_xy is None
                                              else f.outline_xy.tolist())} for f in seg.facets],
        "edges": [{"kind": e.kind, "lengthFt": e.length_m * M_TO_FT} for e in seg.edges],
        "obstructions": [{"kind": o.kind,
                          "estimatedAreaSqFt": o.estimated_area_m2 * M2_TO_FT2}
                         for o in obs],
    }
    accessories = estimate_accessories(accessory_input)
    edge_totals = {k: round(float(v), 1) for k, v in accessories.line_measurements.items()}
    quote_sqft = roof_area_sqft * (1 + waste_pct / 100)
    cost_low = max(min_cents / 100, quote_sqft * price_low / 100)
    cost_high = max(cost_low, quote_sqft * price_high / 100)
    point_density = len(crop.points_local_m) / max(1.0, fp_area_m2)
    confidence = 70
    if point_density >= 10: confidence += 15
    elif point_density >= 5: confidence += 8
    if len(seg.facets) >= 4: confidence += 5
    if seg.notes: confidence -= 10 * len(seg.notes)
    confidence = max(35, min(98, confidence))
    notes = list(seg.notes) + [
        "LiDAR source: %s; point density %.1f pts/m^2." % (crop.source, point_density),
        "Footprint source: %s (%d vertices)." % (fp.source, len(fp.polygon_lonlat) - 1),
        "Total roof area is sum of per-facet planar areas (3D, pitch-corrected).",
    ]
    return RoofMeasurement(
        roofAreaSqFt=round(roof_area_sqft, 1),
        footprintSqFt=round(footprint_sqft, 1),
        roofingSquares=round(_round_up_to_third_square(roof_area_sqft), 2),
        suggestedWastePercent=waste_pct,
        quoteReadySqFt=round(quote_sqft, 1),
        quoteReadySquares=round(_round_up_to_third_square(quote_sqft), 2),
        estimatedCostLow=round(cost_low, 0),
        estimatedCostHigh=round(cost_high, 0),
        pitchSummary=("Predominant pitch %s; weighted average across %d facets. "
                      "Area derived from LiDAR plane fits."
                      % (predominant_pitch_str, len(seg.facets))),
        predominantPitch=predominant_pitch_str,
        facetCount=len(seg.facets),
        sourceSummary=("Address geocoded via %s; footprint from %s; "
                       "roof geometry from %s LiDAR (%d points)."
                       % (gc.source, fp.source, crop.source, len(crop.points_local_m))),
        locationSummary={"lat": gc.lat, "lng": gc.lon, "countrySupport": "USA/Canada",
                         "propertyClass": "residential"},
        disclaimer=("Automated roof measurement from open-data LiDAR + building footprints. "
                    "Verify on-site before ordering materials, filing insurance, or permits."),
        confidenceScore=confidence,
        facets=[{
            "id": f.id, "areaSqFt": round(f.area_m2 * M2_TO_FT2, 1),
            "pitch": "%d/12" % round(f.pitch_x_in_12),
            "pitchExact": round(f.pitch_x_in_12, 2),
            "azimuthDeg": round(f.azimuth_deg, 1),
            "centroid": list(f.centroid),
        } for f in seg.facets],
        edges=[{
            "facetA": e.facet_a, "facetB": e.facet_b,
            "lengthFt": round(e.length_m * M_TO_FT, 1),
            "kind": e.kind, "midpoint": list(e.midpoint),
        } for e in seg.edges],
        obstructions=[{
            "kind": o.kind,
            "estimatedAreaSqFt": round(o.estimated_area_m2 * M2_TO_FT2, 2),
            "heightAbovePlaneFt": round(o.height_above_plane_m * M_TO_FT, 2),
            "centroid": [o.centroid_xy[0], o.centroid_xy[1], o.centroid_z],
        } for o in obs],
        lineMeasurements=edge_totals,
        pitchAreas=pitch_areas,
        facetAreas=[round(a, 1) for a in facet_areas_sqft],
        measurementNotes=notes,
        dataSources={"geocoder": gc.source, "footprint": fp.source,
                     "lidar": crop.source, "lidarTile": crop.source_tile,
                     "lidarYear": crop.captured_year},
        accessoryTakeoff=[
            {"name": it.name, "value": it.value, "unit": it.unit,
             "source": it.source, "note": it.note}
            for it in accessories.material_takeoff
        ],
        obstructionSummary=accessories.obstruction_summary,
        imagery={
            "streetViewUrl": imagery.street_view_url,
            "streetViewPath": imagery.street_view_path,
            "streetViewHeading": imagery.street_view_heading,
            "streetViewAvailable": imagery.street_view_available,
            "aerialUrl": imagery.aerial_url,
            "aerialPath": imagery.aerial_path,
            "aerialZoom": imagery.aerial_zoom,
            "note": imagery.note,
        },
    )


def _synthetic_cloud_for_footprint(fp, lon0, lat0):
    poly_local = [_local_meters(lon, lat, lon0, lat0) for lon, lat in fp.polygon_lonlat]
    xs = [p[0] for p in poly_local]; ys = [p[1] for p in poly_local]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    cx = (minx + maxx) / 2
    cy = (miny + maxy) / 2
    dx = (maxx - minx) / 2
    dy = (maxy - miny) / 2
    eave_z = 4.0
    if dx >= dy:
        half_l, half_w = dx, dy
        ridge_axis = "x"
    else:
        half_l, half_w = dy, dx
        ridge_axis = "y"
    rise = half_w * 0.5
    ridge_z = eave_z + rise
    R = max(0.0, half_l - half_w)
    sw = (cx - dx, cy - dy, eave_z)
    se = (cx + dx, cy - dy, eave_z)
    ne = (cx + dx, cy + dy, eave_z)
    nw = (cx - dx, cy + dy, eave_z)
    if ridge_axis == "x":
        rw = (cx - R, cy, ridge_z)
        re = (cx + R, cy, ridge_z)
        facets = [
            {"corners": [sw, se, re, rw]},
            {"corners": [ne, nw, rw, re]},
            {"corners": [se, ne, re]},
            {"corners": [sw, rw, nw]},
        ]
    else:
        rs = (cx, cy - R, ridge_z)
        rn = (cx, cy + R, ridge_z)
        facets = [
            {"corners": [sw, se, rs]},
            {"corners": [ne, nw, rn]},
            {"corners": [se, ne, rn, rs]},
            {"corners": [nw, sw, rs, rn]},
        ]
    roof_pts = synthesize_test_pointcloud(facets, point_density_per_m2=15, noise_m=0.03)
    rng = np.random.default_rng(7)
    # Generate ~30% as many ground points as roof points so the percentile-based
    # ground filter sees a clean separation between ground level and eave.
    n_ground = max(400, int(len(roof_pts) * 0.30))
    bbox_area = max(1.0, (maxx - minx) * (maxy - miny))
    ground = np.column_stack([
        rng.uniform(minx, maxx, n_ground),
        rng.uniform(miny, maxy, n_ground),
        rng.uniform(-0.1, 0.1, n_ground),
    ])
    return np.vstack([roof_pts, ground])
