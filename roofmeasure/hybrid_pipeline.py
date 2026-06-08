"""Hybrid LIDAR + Google Solar API measurement pipeline.

Combines the best of both:
  - LIDAR (when available): authoritative for facet count, pitch, area, normals.
    True 3D geometry sampled at the actual surface. ±2% area accuracy.
  - Google Solar API (always tried in parallel where Solar coverage exists):
    authoritative for polygon outlines (DSM-based), obstructions (chimneys,
    vents, skylights), and for filling in non-LIDAR areas (Canada).

The merge strategy:

    if LIDAR succeeded and produced >=3 facets:
        Use LIDAR facets for ALL geometric metrics (area, pitch, count).
        Use Solar polygons for the sketch ONLY (they're spatially registered).
        Use Solar for obstructions.

    elif Solar succeeded:
        Use Solar for everything. (Whitby ON, etc.)

    else:
        Return failure.  (NO EagleView fallback — Bill's rule.)

This module DEPENDS on the existing `roofmeasure.providers.google_solar`
adapter. If that import fails (e.g. running v3 modules standalone), the
hybrid path falls back to "LIDAR only" mode and works fine where LIDAR
exists.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .footprint_v2 import BuildingFootprint, get_building_footprint
from .lidar_v2_raw import LidarCrop, fetch_lidar_for_footprint
from .segmentation_v2 import RoofSegmentation, segment_roof

# Optional Solar adapter import — never fail v3 modules if it's not there.
try:
    from .providers.google_solar import measure_via_solar_api  # type: ignore
    HAS_SOLAR = True
except Exception:
    try:
        from .google_solar import measure_via_solar_api  # type: ignore
        HAS_SOLAR = True
    except Exception:
        HAS_SOLAR = False

LOG = logging.getLogger(__name__)


@dataclass
class HybridFacet:
    """A merged facet — geometry from LIDAR, polygon from Solar (when both exist)."""
    id: int
    area_m2: float
    pitch_deg: float
    pitch_x_in_12: float
    azimuth_deg: float
    centroid_lonlat: Optional[Tuple[float, float]]
    polygon_lonlat: Optional[List[Tuple[float, float]]] = None
    provenance: str = "lidar"  # "lidar" | "solar" | "lidar+solar"


@dataclass
class HybridResult:
    success: bool
    primary_source: str  # "lidar" | "solar" | "lidar+solar" | "none"
    total_area_m2: float
    predominant_pitch_x_in_12: float
    predominant_pitch_deg: float
    facets: List[HybridFacet]
    obstructions: List[dict] = field(default_factory=list)
    footprint: Optional[BuildingFootprint] = None
    notes: List[str] = field(default_factory=list)
    lidar_crop: Optional[LidarCrop] = None
    duration_s: float = 0.0


# ---------------------------------------------------------------------------
# Spatial helpers
# ---------------------------------------------------------------------------

def _haversine_m(lon1, lat1, lon2, lat2):
    """Great-circle distance in meters (small-distance approx)."""
    import math
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    dx = (lon2 - lon1) * m_per_deg_lon
    dy = (lat2 - lat1) * m_per_deg_lat
    return math.hypot(dx, dy)


def _match_lidar_to_solar_polygons(
    lidar_facets: List[HybridFacet],
    solar_facets: List[dict],
) -> List[HybridFacet]:
    """For each LIDAR facet, find the closest Solar polygon by centroid distance.

    Attaches the Solar polygon to the LIDAR facet if they're within 8 meters.
    LIDAR facets without a matched polygon keep `polygon_lonlat=None`.
    """
    if not solar_facets:
        return lidar_facets

    # Solar adapter typically returns a list of dicts with 'centroid_lonlat' and 'polygon_lonlat'
    out = []
    used = set()
    for lf in lidar_facets:
        if lf.centroid_lonlat is None:
            out.append(lf)
            continue
        best_idx = None
        best_dist = 8.0  # 8 meter match threshold
        for i, sf in enumerate(solar_facets):
            if i in used:
                continue
            sc = sf.get("centroid_lonlat") or sf.get("centroid")
            if sc is None or len(sc) < 2:
                continue
            dist = _haversine_m(lf.centroid_lonlat[0], lf.centroid_lonlat[1],
                                sc[0], sc[1])
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        if best_idx is not None:
            sf = solar_facets[best_idx]
            poly = sf.get("polygon_lonlat") or sf.get("polygon")
            out.append(HybridFacet(
                id=lf.id,
                area_m2=lf.area_m2,
                pitch_deg=lf.pitch_deg,
                pitch_x_in_12=lf.pitch_x_in_12,
                azimuth_deg=lf.azimuth_deg,
                centroid_lonlat=lf.centroid_lonlat,
                polygon_lonlat=poly,
                provenance="lidar+solar",
            ))
            used.add(best_idx)
        else:
            out.append(lf)
    return out


# ---------------------------------------------------------------------------
# LIDAR → HybridFacets
# ---------------------------------------------------------------------------

def _lidar_seg_to_hybrid(
    seg: RoofSegmentation,
    crop: LidarCrop,
) -> List[HybridFacet]:
    """Convert v2.1 RoofSegmentation facets to HybridFacet (with lon/lat centroids)."""
    from math import cos, radians
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * cos(radians(crop.crs_origin_lonlat[1]))

    hf_list = []
    for f in seg.facets:
        ce, cn, _ = f.centroid
        cent_lon = crop.crs_origin_lonlat[0] + ce / m_per_deg_lon
        cent_lat = crop.crs_origin_lonlat[1] + cn / m_per_deg_lat
        hf_list.append(HybridFacet(
            id=f.id,
            area_m2=f.area_m2,
            pitch_deg=f.pitch_deg,
            pitch_x_in_12=f.pitch_x_in_12,
            azimuth_deg=f.azimuth_deg,
            centroid_lonlat=(cent_lon, cent_lat),
            polygon_lonlat=None,
            provenance="lidar",
        ))
    return hf_list


# ---------------------------------------------------------------------------
# Solar adapter result → HybridFacets
# ---------------------------------------------------------------------------


def _merge_hybrid_facets(facets, angle_tol_deg=10.0, pitch_tol_x12=2.0, centroid_dist_m=15.0):
    """Merge HybridFacets that share orientation + are physically close.
    Solar API over-segments — adjacent coplanar regions get separate stats.
    Same union-find approach as segmentation_v2._merge_coplanar_facets.
    """
    import math as _math
    n = len(facets)
    if n <= 1:
        return facets
    parent = list(range(n))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def union(i, j):
        pi, pj = find(i), find(j)
        if pi != pj: parent[pi] = pj
    def _az_diff(a, b):
        d = abs(a - b) % 360.0
        return min(d, 360.0 - d)
    def _haversine_m(lon1, lat1, lon2, lat2):
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * _math.cos(_math.radians((lat1 + lat2) / 2))
        return _math.hypot((lon2 - lon1) * m_per_deg_lon, (lat2 - lat1) * m_per_deg_lat)
    for i in range(n):
        for j in range(i + 1, n):
            fa, fb = facets[i], facets[j]
            if abs(fa.pitch_x_in_12 - fb.pitch_x_in_12) > pitch_tol_x12:
                continue
            if _az_diff(fa.azimuth_deg, fb.azimuth_deg) > angle_tol_deg:
                continue
            if fa.centroid_lonlat and fb.centroid_lonlat:
                d = _haversine_m(fa.centroid_lonlat[0], fa.centroid_lonlat[1],
                                 fb.centroid_lonlat[0], fb.centroid_lonlat[1])
                if d > centroid_dist_m:
                    continue
            union(i, j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    merged = []
    next_id = 0
    for _, indices in groups.items():
        if len(indices) == 1:
            f = facets[indices[0]]
            f.id = next_id
            merged.append(f); next_id += 1
            continue
        # Combine
        members = [facets[i] for i in indices]
        total_area = sum(m.area_m2 for m in members)
        w_pitch_deg = sum(m.area_m2 * m.pitch_deg for m in members) / max(total_area, 1e-6)
        w_pitch_x12 = sum(m.area_m2 * m.pitch_x_in_12 for m in members) / max(total_area, 1e-6)
        # Azimuth needs circular-mean (handle 350 + 10 averaging to 0, not 180)
        import math as _m2
        sx = sum(m.area_m2 * _m2.sin(_m2.radians(m.azimuth_deg)) for m in members)
        cy = sum(m.area_m2 * _m2.cos(_m2.radians(m.azimuth_deg)) for m in members)
        w_az = _m2.degrees(_m2.atan2(sx, cy)) % 360.0
        # Centroid: area-weighted
        c_lons = [m.centroid_lonlat[0] for m in members if m.centroid_lonlat]
        c_lats = [m.centroid_lonlat[1] for m in members if m.centroid_lonlat]
        if c_lons:
            cent = (sum(c_lons) / len(c_lons), sum(c_lats) / len(c_lats))
        else:
            cent = members[0].centroid_lonlat
        from .hybrid_pipeline import HybridFacet
        merged.append(HybridFacet(
            id=next_id, area_m2=total_area,
            pitch_deg=w_pitch_deg, pitch_x_in_12=w_pitch_x12,
            azimuth_deg=w_az, centroid_lonlat=cent,
            polygon_lonlat=None, provenance=members[0].provenance + "+merged",
        ))
        next_id += 1
    return merged

def _solar_result_to_hybrid_v38(solar_result):
    """v3.8 raw solar parser — accepts the raw Solar API JSON response."""
    import math
    if not isinstance(solar_result, dict):
        return [], []
    sp = solar_result.get("solarPotential") or {}
    segs = sp.get("roofSegmentStats") or []
    facets_out = []
    for i, seg in enumerate(segs):
        stats = seg.get("stats") or {}
        area = stats.get("areaMeters2") or seg.get("stats", {}).get("areaMeters2")
        pitch_deg = float(seg.get("pitchDegrees", 0))
        az_deg = float(seg.get("azimuthDegrees", 0))
        center = seg.get("center") or {}
        plane_h = seg.get("planeHeightAtCenterMeters")
        cent_lon = center.get("longitude")
        cent_lat = center.get("latitude")
        try:
            from .hybrid_pipeline import HybridFacet
        except ImportError:
            HybridFacet = None
        if area is None or HybridFacet is None:
            continue
        pitch_x12 = math.tan(math.radians(pitch_deg)) * 12
        facets_out.append(HybridFacet(
            id=i, area_m2=float(area), pitch_deg=pitch_deg, pitch_x_in_12=pitch_x12,
            azimuth_deg=az_deg,
            centroid_lonlat=(cent_lon, cent_lat) if (cent_lon and cent_lat) else None,
            polygon_lonlat=None, provenance="solar",
        ))
    return facets_out, []

def _solar_result_to_hybrid(solar_result) -> Tuple[List[HybridFacet], List[dict]]:
    """Normalize a Solar-API result into (facets, obstructions).

    The existing google_solar.measure_via_solar_api returns various shapes
    depending on engine version. We probe attributes / keys to be resilient.
    """
    facets_in = (
        getattr(solar_result, "facets", None)
        or getattr(solar_result, "roof_facets", None)
        or []
    )
    obstructions = (
        getattr(solar_result, "obstructions", None)
        or []
    )

    out = []
    for i, sf in enumerate(facets_in):
        # Try multiple field names — Solar adapter has evolved
        area = (
            getattr(sf, "area_m2", None)
            or getattr(sf, "area_meters_2", None)
            or sf.get("area_m2", None) if isinstance(sf, dict) else None
        )
        pitch = (
            getattr(sf, "pitch_deg", None)
            or getattr(sf, "pitch_degrees", None)
            or sf.get("pitch_deg", None) if isinstance(sf, dict) else None
        )
        az = (
            getattr(sf, "azimuth_deg", None)
            or getattr(sf, "azimuth_degrees", None)
            or sf.get("azimuth_deg", None) if isinstance(sf, dict) else None
        )
        centroid = (
            getattr(sf, "centroid_lonlat", None)
            or getattr(sf, "center", None)
            or sf.get("centroid_lonlat", None) if isinstance(sf, dict) else None
        )
        polygon = (
            getattr(sf, "polygon_lonlat", None)
            or getattr(sf, "polygon", None)
            or sf.get("polygon_lonlat", None) if isinstance(sf, dict) else None
        )

        if area is None:
            continue
        import math
        pitch_deg = float(pitch) if pitch is not None else 0.0
        pitch_x12 = math.tan(math.radians(pitch_deg)) * 12 if pitch_deg else 0.0

        out.append(HybridFacet(
            id=i,
            area_m2=float(area),
            pitch_deg=pitch_deg,
            pitch_x_in_12=pitch_x12,
            azimuth_deg=float(az) if az is not None else 0.0,
            centroid_lonlat=tuple(centroid) if centroid else None,
            polygon_lonlat=list(polygon) if polygon else None,
            provenance="solar",
        ))

    obs_out = []
    for ob in obstructions:
        if isinstance(ob, dict):
            obs_out.append(ob)
        else:
            obs_out.append({k: getattr(ob, k, None) for k in ("kind", "polygon_lonlat", "area_m2")})

    return out, obs_out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def measure_hybrid(lat: float, lon: float) -> HybridResult:
    """End-to-end hybrid pipeline. Always returns a HybridResult (never raises)."""
    start = time.time()
    notes: List[str] = []

    # 1. Footprint (shared by both providers)
    fp = get_building_footprint(lat, lon)
    if fp is None:
        return HybridResult(
            success=False, primary_source="none",
            total_area_m2=0, predominant_pitch_x_in_12=0,
            predominant_pitch_deg=0, facets=[],
            notes=["no footprint"], duration_s=time.time() - start,
        )
    notes.append(f"footprint src={fp.source} vertices={len(fp.polygon_lonlat)}")

    # 2. LIDAR attempt
    lidar_facets: List[HybridFacet] = []
    crop = fetch_lidar_for_footprint(fp)
    if crop is None:
        try:
            from .nrcan_hrdem_provider import fetch_lidar_for_footprint as _nrcan
            crop = _nrcan(fp)
            if crop is not None:
                notes.append(f"nrcan_hrdem: {len(crop.points_local_m)} pts ({crop.point_density_per_m2:.1f}/m^2)")
        except Exception as _e:
            notes.append(f"nrcan err: {_e}")
    if crop is not None:
        notes.append(
            f"lidar n={len(crop.points_local_m)} density={crop.point_density_per_m2:.1f}/m^2 "
            f"z_unit={crop.z_unit_detected} classes_used={crop.classifications_used}"
        )
        from .footprint_v2 import polygon_area_m2 as _poly_area
        _fp_area = _poly_area(fp.polygon_lonlat)
        seg = segment_roof(
            crop.points_local_m,
            density_hint=crop.point_density_per_m2,
            points_already_filtered=crop.classifications_used,
            footprint_area_m2=_fp_area,
            footprint_vertex_count=len(fp.polygon_lonlat),
        )
        notes.extend(seg.notes)
        lidar_facets = _lidar_seg_to_hybrid(seg, crop)
        notes.append(f"lidar yielded {len(lidar_facets)} facets")
    else:
        notes.append("no lidar")

    # 3. Solar attempt (in parallel ideally; sequential here for simplicity)
    solar_facets: List[HybridFacet] = []
    obstructions: List[dict] = []
    if HAS_SOLAR:
        try:
            # v3.8 direct solar — bypass broken adapter signature
            import os as _so_os
            import requests as _so_req
            _solar_key = _so_os.environ.get('GOOGLE_SOLAR_API_KEY') or _so_os.environ.get('GOOGLE_MAPS_API_KEY')
            if _solar_key:
                _solar_resp = _so_req.get(
                    'https://solar.googleapis.com/v1/buildingInsights:findClosest',
                    params={'location.latitude': lat, 'location.longitude': lon,
                            'requiredQuality': 'HIGH', 'key': _solar_key},
                    timeout=30,
                )
                if _solar_resp.status_code == 200:
                    solar_result = _solar_resp.json()
                else:
                    raise Exception(f'Solar API HTTP {_solar_resp.status_code}: {_solar_resp.text[:200]}')
            else:
                solar_result = None
            if solar_result is not None:
                solar_facets, obstructions = _solar_result_to_hybrid_v38(solar_result)
            # v3.9 merge solar — Solar API over-segments, fold coplanar facets together
            _pre_merge = len(solar_facets)
            solar_facets = _merge_hybrid_facets(solar_facets, angle_tol_deg=22.0, pitch_tol_x12=5.0, centroid_dist_m=25.0)
            # v3.10 absorb tiny — drop facets < 5% of total area (Solar over-segments these)
            if solar_facets:
                _total = sum(f.area_m2 for f in solar_facets)
                _kept = [f for f in solar_facets if f.area_m2 >= max(2.0, 0.05 * _total)]
                if len(_kept) != len(solar_facets):
                    notes.append(f"solar dropped {len(solar_facets)-len(_kept)} tiny facets (<5% of total)")
                    solar_facets = _kept
                    for _i, _f in enumerate(solar_facets): _f.id = _i
            if len(solar_facets) != _pre_merge:
                notes.append(f"solar facets merged {_pre_merge} -> {len(solar_facets)}")
                notes.append(f"solar yielded {len(solar_facets)} facets, {len(obstructions)} obstructions")
            else:
                notes.append("solar returned None")
        except Exception as e:
            notes.append(f"solar failed: {e}")
    else:
        notes.append("solar adapter not importable in this environment")

    # 4. Merge
    if len(lidar_facets) >= 3:
        # LIDAR is authoritative; attach Solar polygons
        merged = _match_lidar_to_solar_polygons(lidar_facets, [
            {
                "centroid_lonlat": sf.centroid_lonlat,
                "polygon_lonlat": sf.polygon_lonlat,
            }
            for sf in solar_facets
        ])
        primary = "lidar+solar" if solar_facets else "lidar"
    elif solar_facets:
        merged = solar_facets
        primary = "solar"
    elif lidar_facets:
        # LIDAR found <3 facets but no Solar — use LIDAR anyway
        merged = lidar_facets
        primary = "lidar"
    else:
        return HybridResult(
            success=False, primary_source="none",
            total_area_m2=0, predominant_pitch_x_in_12=0,
            predominant_pitch_deg=0, facets=[],
            notes=notes + ["both providers failed"],
            footprint=fp, lidar_crop=crop,
            duration_s=time.time() - start,
        )

    total = sum(f.area_m2 for f in merged)
    w_pitch12 = sum(f.area_m2 * f.pitch_x_in_12 for f in merged) / max(total, 1e-6)
    w_pitch_deg = sum(f.area_m2 * f.pitch_deg for f in merged) / max(total, 1e-6)

    notes.append(f"final: primary={primary}, {len(merged)} facets, {total:.1f}m^2")

    return HybridResult(
        success=True,
        primary_source=primary,
        total_area_m2=total,
        predominant_pitch_x_in_12=w_pitch12,
        predominant_pitch_deg=w_pitch_deg,
        facets=merged,
        obstructions=obstructions,
        footprint=fp,
        lidar_crop=crop,
        notes=notes,
        duration_s=time.time() - start,
    )
