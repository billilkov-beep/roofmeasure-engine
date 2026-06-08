"""measurement.py wireup — v3 (passes density + classifications hint to segmentation).

Drop into roofmeasure/ alongside footprint_v2.py, lidar_v2_raw.py, segmentation_v2.py.

Public function: `measure_via_lidar_v2(lat, lon) -> MeasurementResult | None`
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from .footprint_v2 import BuildingFootprint, get_building_footprint, polygon_area_m2
from .lidar_v2_raw import LidarCrop, fetch_lidar_for_footprint
from .segmentation_v2 import RoofSegmentation, segment_roof

LOG = logging.getLogger(__name__)


@dataclass
class FacetSummary:
    id: int
    area_m2: float
    pitch_deg: float
    pitch_x_in_12: float
    azimuth_deg: float
    centroid_lonlat: Optional[tuple] = None


@dataclass
class LineMeasurements:
    eaves_m: float
    rakes_m: float
    drip_edge_m: float
    ridges_hips_m: float
    valleys_m: float


@dataclass
class MeasurementResult:
    success: bool
    source: str
    total_area_m2: float
    predominant_pitch_x_in_12: float
    predominant_pitch_deg: float
    facets: List[FacetSummary]
    line_measurements: Optional[LineMeasurements]
    footprint: Optional[BuildingFootprint]
    notes: List[str]
    duration_s: float


def measure_via_lidar_v2(lat: float, lon: float) -> Optional[MeasurementResult]:
    start = time.time()
    notes: List[str] = []

    fp = get_building_footprint(lat, lon)
    if fp is None:
        return MeasurementResult(
            success=False, source="lidar_v2_1",
            total_area_m2=0, predominant_pitch_x_in_12=0,
            predominant_pitch_deg=0, facets=[], line_measurements=None,
            footprint=None, notes=["no footprint"], duration_s=time.time() - start,
        )
    notes.append(f"footprint source={fp.source}, vertices={len(fp.polygon_lonlat)}")

    crop = fetch_lidar_for_footprint(fp)
    if crop is None:
        return MeasurementResult(
            success=False, source="lidar_v2_1",
            total_area_m2=0, predominant_pitch_x_in_12=0,
            predominant_pitch_deg=0, facets=[], line_measurements=None,
            footprint=fp, notes=notes + ["no LIDAR (out-of-coverage)"],
            duration_s=time.time() - start,
        )
    notes.append(
        f"lidar src={crop.source}, n={len(crop.points_local_m)}, "
        f"density={crop.point_density_per_m2:.1f}/m^2, z_unit={crop.z_unit_detected}, "
        f"year={crop.captured_year}, classes_used={crop.classifications_used}"
    )

    fp_area = polygon_area_m2(fp.polygon_lonlat)
    seg = segment_roof(
        crop.points_local_m,
        density_hint=crop.point_density_per_m2,
        points_already_filtered=crop.classifications_used,
        footprint_area_m2=fp_area,
        footprint_vertex_count=len(fp.polygon_lonlat),
    )
    notes.extend(seg.notes)
    if not seg.facets:
        return MeasurementResult(
            success=False, source="lidar_v2_1",
            total_area_m2=0, predominant_pitch_x_in_12=0,
            predominant_pitch_deg=0, facets=[], line_measurements=None,
            footprint=fp, notes=notes + ["segmentation 0 facets"],
            duration_s=time.time() - start,
        )

    from math import cos, radians
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * cos(radians(crop.crs_origin_lonlat[1]))

    facet_summaries = []
    total_area_m2 = 0.0
    for f in seg.facets:
        ce, cn, _ = f.centroid
        cent_lon = crop.crs_origin_lonlat[0] + ce / m_per_deg_lon
        cent_lat = crop.crs_origin_lonlat[1] + cn / m_per_deg_lat
        facet_summaries.append(FacetSummary(
            id=f.id, area_m2=f.area_m2,
            pitch_deg=f.pitch_deg, pitch_x_in_12=f.pitch_x_in_12,
            azimuth_deg=f.azimuth_deg, centroid_lonlat=(cent_lon, cent_lat),
        ))
        total_area_m2 += f.area_m2

    w_pitch12 = sum(f.area_m2 * f.pitch_x_in_12 for f in seg.facets) / max(total_area_m2, 1e-6)
    w_pitch_deg = sum(f.area_m2 * f.pitch_deg for f in seg.facets) / max(total_area_m2, 1e-6)

    notes.append(f"v2.1: {len(seg.facets)} facets, total {total_area_m2:.1f}m^2")
    return MeasurementResult(
        success=True, source="lidar_v2_1",
        total_area_m2=total_area_m2,
        predominant_pitch_x_in_12=w_pitch12,
        predominant_pitch_deg=w_pitch_deg,
        facets=facet_summaries, line_measurements=None,
        footprint=fp, notes=notes, duration_s=time.time() - start,
    )
