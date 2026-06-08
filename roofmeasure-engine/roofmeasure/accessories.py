"""Accessory estimation: fill in the fields LiDAR can't directly measure.

This module takes a measurement-engine result and returns:
  - rakes (sloped boundary edges, split from "eave" when not directly classified)
  - flashing (roof-to-wall, e.g. chimney sides and tops)
  - step flashing (roof-to-vertical-wall along a slope)
  - gutters / downspouts (gutter length ~= eave length)
  - drip edge pieces, starter strips, ridge cap (linear footage rolled into pieces)
  - underlayment, ice/water shield (rolls)
  - shingle bundles, nails (material take-off)
  - vent boots / pipe collars (one per penetration)

Every estimate is tagged with `source = "measured" | "estimated" | "derived"` so the
report can show the customer what came from real geometry vs. a formula. This is the
same kind of breakdown EagleView puts in a verified report.

CAVEAT: the formula-based items (flashing, step flashing, dormer count) are educated
guesses based on roof complexity. They scale with sqrt(roof area) which captures the
typical relationship between roof size and linear features, but they cannot replace
on-site verification. They're meant to give a contractor a ballpark for material
ordering, not for insurance claims.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

M2_TO_FT2 = 10.7639
M_TO_FT = 3.28084


@dataclass
class AccessoryItem:
    """One line item in the accessories block."""
    name: str
    value: float
    unit: str                       # "ft" | "sqft" | "count" | "bundles" | "rolls" | "pieces" | "lbs"
    source: str                     # "measured" | "estimated" | "derived"
    note: str = ""


@dataclass
class AccessoryEstimate:
    """Full accessories breakdown for a single roof."""
    line_measurements: Dict[str, float] = field(default_factory=dict)
    material_takeoff: List[AccessoryItem] = field(default_factory=list)
    obstruction_summary: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lineMeasurements": self.line_measurements,
            "materialTakeoff": [asdict(item) for item in self.material_takeoff],
            "obstructionSummary": self.obstruction_summary,
        }


# ---------------------------------------------------------------------------
# Boundary edge classifier (rake vs. eave)
# ---------------------------------------------------------------------------

def _split_boundary_into_rakes_and_eaves(facets, edges, eave_total_ft: float) -> Dict[str, float]:
    """Given facets and edges, partition the 'eave/boundary' edges into rakes vs eaves.

    Heuristic: for each facet's boundary, segments are 'rake' if their z-span exceeds
    a threshold (i.e. they slope significantly), else 'eave'. When we don't have full
    3D outline data, fall back to a complexity-based fraction.

    Returns {"eavesFt": ..., "rakesFt": ...} that sums to the original eave_total_ft.
    """
    if eave_total_ft <= 0:
        return {"eavesFt": 0.0, "rakesFt": 0.0}

    # Heuristic: gable-style triangular facets contribute rakes; hip-style trapezoids contribute eaves.
    # A facet is "gable-like" if it has only one boundary side at the eave height and the rest
    # rises to a ridge point. In our convex-hull outline this manifests as a steep aspect ratio.
    triangular_facets = 0
    trapezoidal_facets = 0
    for f in facets or []:
        outline = f.get("outline_xy") if isinstance(f, dict) else None
        if outline is not None and len(outline) <= 3:
            triangular_facets += 1
        else:
            trapezoidal_facets += 1

    total_facets = max(1, triangular_facets + trapezoidal_facets)
    rake_fraction = triangular_facets / total_facets
    # Bound it: even a pure gable roof has eaves on the long sides
    rake_fraction = min(0.55, max(0.0, rake_fraction))

    rakes_ft = round(eave_total_ft * rake_fraction, 1)
    eaves_ft = round(eave_total_ft - rakes_ft, 1)
    return {"eavesFt": eaves_ft, "rakesFt": rakes_ft}


# ---------------------------------------------------------------------------
# Top-level estimator
# ---------------------------------------------------------------------------

def estimate_accessories(measurement: Dict[str, Any]) -> AccessoryEstimate:
    """Fill in accessory line items + material take-off for a measurement.

    `measurement` is the dict produced by RoofMeasurement.asdict() (or equivalent).
    We read what's there and estimate what isn't.
    """
    roof_area_sqft = float(measurement.get("roofAreaSqFt") or 0.0)
    footprint_sqft = float(measurement.get("footprintSqFt") or 0.0)
    facets = measurement.get("facets") or []
    edges = measurement.get("edges") or []
    obstructions = measurement.get("obstructions") or []
    line = measurement.get("lineMeasurements") or {}

    # ---- Already-measured fields from LiDAR ----
    ridges_ft = float(line.get("ridgesFt") or 0.0)
    hips_ft = float(line.get("hipsFt") or 0.0)
    valleys_ft = float(line.get("valleysFt") or 0.0)
    rakes_ft = float(line.get("rakesFt") or 0.0)
    raw_eaves_ft = float(line.get("eavesFt") or 0.0)

    # ---- Rake / eave split ----
    # If the segmentation already separated them, use it; else split the lumped boundary.
    if rakes_ft <= 0 and raw_eaves_ft > 0:
        split = _split_boundary_into_rakes_and_eaves(facets, edges, raw_eaves_ft)
        eaves_ft = split["eavesFt"]
        rakes_ft = split["rakesFt"]
        rake_source = "estimated"
    else:
        eaves_ft = raw_eaves_ft
        rake_source = "measured"

    # ---- Penetrations / obstructions ----
    penetration_count = int(line.get("penetrations") or len(obstructions))
    penetration_area = float(line.get("penetrationsAreaSqFt") or
                             sum(o.get("estimatedAreaSqFt", 0) for o in obstructions))

    # ---- Flashing & step flashing (formula-based; LiDAR can't reliably measure these) ----
    # The existing JS heuristic uses: sqrt(area) * 0.32 + small noise. We keep the same
    # scale but add complexity from chimney count (more chimneys -> more flashing).
    chimney_count = sum(1 for o in obstructions if (o.get("kind") or "") == "chimney")
    root = math.sqrt(max(0.0, roof_area_sqft))
    base_flashing = root * 0.30                            # ft, scales with sqrt(area)
    chimney_flashing = chimney_count * 12                  # 12 ft of flashing per chimney (perimeter ~ 4ft x 3 sides)
    flashing_ft = round(base_flashing + chimney_flashing, 1)
    step_flashing_ft = round(rakes_ft * 0.20 + root * 0.15, 1)  # along rakes against walls

    # ---- Gutters / downspouts ----
    # Gutter length ~= eaves length (gutters live on horizontal eaves only)
    gutters_ft = round(eaves_ft, 1)
    # Downspouts: one per 30 ft of gutter, rounded up, minimum 2
    downspout_count = max(2, math.ceil(gutters_ft / 30.0)) if gutters_ft > 0 else 0

    # ---- Roll into a line_measurements dict matching the existing schema ----
    drip_edge_ft = round(eaves_ft + rakes_ft, 1)
    line_measurements = {
        "ridgesFt": ridges_ft,
        "hipsFt": hips_ft,
        "ridgesHipsFt": round(ridges_ft + hips_ft, 1),
        "valleysFt": valleys_ft,
        "eavesFt": eaves_ft,
        "rakesFt": rakes_ft,
        "dripEdgeFt": drip_edge_ft,
        "flashingFt": flashing_ft,
        "stepFlashingFt": step_flashing_ft,
        "guttersFt": gutters_ft,
        "downspoutCount": float(downspout_count),
        "penetrations": float(penetration_count),
        "penetrationsAreaSqFt": round(penetration_area, 1),
    }

    # ---- Material take-off ----
    # Standard packing: 3 bundles per square (shingles), 35 ft per bundle of starter,
    # 20 ft per bundle of ridge/hip cap, 400 sqft per roll of synthetic underlayment,
    # 65 sqft per roll of ice & water shield. Drip edge: 10 ft per piece.
    ridge_cap_ft = round(ridges_ft + hips_ft, 1)
    waste_pct = float(measurement.get("suggestedWastePercent") or 12)
    waste_area = roof_area_sqft * (1 + waste_pct / 100)

    # Ice & water shield: a 3 ft strip along all eaves, plus 2 ft up valleys
    ice_water_sqft = round(eaves_ft * 3.0 + valleys_ft * 4.0, 1)

    # Roof nails: ~2 lbs per square, plus 0.5 lbs/sq overhead
    squares = waste_area / 100.0
    nails_lbs = round(squares * 2.5, 1)

    items: List[AccessoryItem] = []

    def add(name, value, unit, source, note=""):
        items.append(AccessoryItem(name=name, value=value, unit=unit, source=source, note=note))

    # Coverage / surface items
    add("Roof surface area", round(roof_area_sqft, 1), "sqft", "measured",
        "3D pitch-corrected area from LiDAR plane segmentation")
    add("Waste-adjusted shingle area", round(waste_area, 1), "sqft", "derived",
        f"Roof area + {int(waste_pct)}% suggested waste")
    add("Shingle squares (3-tab/architectural)", round(math.ceil(waste_area / 100 * 3) / 3, 2),
        "count", "derived", "Squares = sqft / 100, rounded up to nearest 1/3")
    add("Shingle bundles (3 per square)", math.ceil(squares * 3), "bundles", "derived",
        "Most architectural shingles ship 3 bundles per square")
    add("Synthetic underlayment", math.ceil(waste_area / 400), "rolls", "derived",
        "400 sqft per roll typical (e.g. RhinoRoof)")
    add("Ice & water shield", math.ceil(ice_water_sqft / 65), "rolls", "derived",
        f"3 ft along {int(eaves_ft)} ft of eaves + 2 ft up valleys; 65 sqft per roll")

    # Linear features
    add("Ridge cap material", ridge_cap_ft, "ft", "measured" if (ridges_ft + hips_ft) > 0 else "estimated",
        "Caps for ridges + hips. ~20 ft per bundle of ridge cap.")
    add("Ridge cap bundles", math.ceil(ridge_cap_ft / 20) if ridge_cap_ft > 0 else 0, "bundles", "derived",
        "20 ft per bundle typical")
    add("Starter strip", round(eaves_ft + rakes_ft, 1), "ft", "measured" if eaves_ft + rakes_ft > 0 else "estimated",
        "Along all eaves + rakes")
    add("Starter strip bundles", math.ceil((eaves_ft + rakes_ft) / 35) if eaves_ft + rakes_ft > 0 else 0,
        "bundles", "derived", "35 ft per bundle typical")
    add("Drip edge", drip_edge_ft, "ft", "measured" if drip_edge_ft > 0 else "estimated",
        "Eaves + rakes")
    add("Drip edge pieces (10 ft each)", math.ceil(drip_edge_ft / 10) if drip_edge_ft > 0 else 0,
        "pieces", "derived", "10 ft sticks typical")
    add("Step flashing", step_flashing_ft, "ft", "estimated",
        "Where rakes meet walls. Formula-based; verify on site.")
    add("Wall flashing", flashing_ft, "ft", "estimated",
        f"Includes chimney flashing for {chimney_count} chimney(s). Formula-based.")
    add("Gutters", gutters_ft, "ft", "derived",
        "Equal to eaves (gutters live on horizontal eaves only)")
    add("Downspouts", downspout_count, "count", "estimated",
        "One per 30 ft of gutter, minimum 2")

    # Penetrations / accessories
    add("Roof penetrations", penetration_count, "count",
        "measured" if obstructions else "estimated",
        "Vents, chimneys, HVAC, skylights detected from LiDAR residuals")
    add("Pipe boots / vent collars", penetration_count, "count", "derived",
        "One per penetration; specific kind picked at site")
    add("Roof nails", nails_lbs, "lbs", "derived",
        "~2.5 lbs per square including waste")

    # Obstruction summary (count by kind)
    obstruction_summary: Dict[str, int] = {}
    for o in obstructions:
        kind = (o.get("kind") or "unknown").lower()
        obstruction_summary[kind] = obstruction_summary.get(kind, 0) + 1

    return AccessoryEstimate(
        line_measurements=line_measurements,
        material_takeoff=items,
        obstruction_summary=obstruction_summary,
    )
