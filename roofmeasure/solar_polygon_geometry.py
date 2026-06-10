from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

FT_PER_M = 3.28084
R_EARTH_M = 6371000.0


def _project(lat: float, lon: float, lat0: float, lon0: float) -> Tuple[float, float]:
    x = math.radians(lon - lon0) * R_EARTH_M * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R_EARTH_M
    return x * FT_PER_M, y * FT_PER_M


def _dist_ft(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _pitch_num(p: Any) -> float:
    try:
        return float(str(p).split("/")[0])
    except Exception:
        return 0.0


def _edge_key(a: Tuple[float, float], b: Tuple[float, float], snap_ft: float = 3.0) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    p1 = (round(a[0] / snap_ft), round(a[1] / snap_ft))
    p2 = (round(b[0] / snap_ft), round(b[1] / snap_ft))
    return tuple(sorted([p1, p2]))  # type: ignore


def _classify_shared_edge(f1: Dict[str, Any], f2: Dict[str, Any]) -> str:
    p1 = _pitch_num(f1.get("pitch"))
    p2 = _pitch_num(f2.get("pitch"))
    a1 = float(f1.get("azimuthDeg") or 0)
    a2 = float(f2.get("azimuthDeg") or 0)

    diff = abs((a1 - a2 + 180) % 360 - 180)

    if p1 < 3 and p2 < 3:
        return "flatJoint"

    # Opposite roof planes usually create ridge/hip.
    if diff > 120:
        return "ridgeHip"

    # Intersecting roof planes with large direction difference usually valley/hip.
    if diff > 35:
        if max(p1, p2) >= 6:
            return "valley"
        return "ridgeHip"

    return "seam"


def build_diagram_geometry(measurement: Dict[str, Any]) -> Dict[str, Any]:
    facets = measurement.get("facets") or []
    poly_facets = [f for f in facets if f.get("polygon") and len(f.get("polygon") or []) >= 3]

    if not poly_facets:
        measurement["diagramGeometryStatus"] = "needs_manual_review"
        measurement["diagramGeometry"] = {
            "status": "needs_manual_review",
            "source": "none",
            "facets": [],
            "edges": [],
            "message": "Real roof facet polygons were not available. Do not draw fake EagleView-style diagrams."
        }
        measurement.setdefault("measurementNotes", []).append(
            "WARNING: Real roof polygons are missing. EagleView-style diagrams require Data Layers polygon extraction or manual tracing."
        )
        measurement["requiresReview"] = True
        return measurement

    all_pts = []
    for f in poly_facets:
        for lat, lon in f["polygon"]:
            all_pts.append((float(lat), float(lon)))

    lat0 = sum(p[0] for p in all_pts) / len(all_pts)
    lon0 = sum(p[1] for p in all_pts) / len(all_pts)

    diagram_facets: List[Dict[str, Any]] = []
    edge_bucket: Dict[Any, List[Dict[str, Any]]] = {}

    for f in poly_facets:
        pts_latlon = [(float(lat), float(lon)) for lat, lon in f["polygon"]]
        pts_xy = [_project(lat, lon, lat0, lon0) for lat, lon in pts_latlon]

        diagram_facets.append({
            "id": f.get("id"),
            "label": f.get("label") or "",
            "pitch": f.get("pitch"),
            "pitchExact": f.get("pitchExact"),
            "pitchDeg": f.get("pitchDeg"),
            "azimuthDeg": f.get("azimuthDeg"),
            "areaSqFt": f.get("areaSqFt"),
            "polygonLatLon": pts_latlon,
            "polygonXYFt": [[round(x, 2), round(y, 2)] for x, y in pts_xy],
            "centerLat": f.get("centerLat"),
            "centerLon": f.get("centerLon"),
        })

        for i in range(len(pts_xy)):
            a = pts_xy[i]
            b = pts_xy[(i + 1) % len(pts_xy)]
            if _dist_ft(a, b) < 3:
                continue
            k = _edge_key(a, b)
            edge_bucket.setdefault(k, []).append({
                "facetId": f.get("id"),
                "facet": f,
                "a": a,
                "b": b,
                "lengthFt": _dist_ft(a, b),
            })

    edges = []
    totals = {
        "ridgesFt": 0.0,
        "hipsFt": 0.0,
        "ridgesHipsFt": 0.0,
        "valleysFt": 0.0,
        "rakesFt": 0.0,
        "eavesFt": 0.0,
        "dripEdgeFt": 0.0,
    }

    for parts in edge_bucket.values():
        longest = max(parts, key=lambda e: e["lengthFt"])
        length = longest["lengthFt"]
        a = longest["a"]
        b = longest["b"]

        if len(parts) >= 2:
            kind = _classify_shared_edge(parts[0]["facet"], parts[1]["facet"])
            if kind == "valley":
                totals["valleysFt"] += length
            elif kind == "ridgeHip":
                totals["ridgesHipsFt"] += length
                totals["ridgesFt"] += length
            edge_type = kind
        else:
            f = longest["facet"]
            pitch = _pitch_num(f.get("pitch"))
            # Boundary edge. Low/flat-ish bottom edges are eaves; sloped/steep are rakes.
            if pitch >= 3:
                # Use edge angle to split eave/rake. Horizontal-ish edges are more likely eaves.
                ang = abs(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))) % 180
                if ang < 25 or ang > 155:
                    totals["eavesFt"] += length
                    edge_type = "eave"
                else:
                    totals["rakesFt"] += length
                    edge_type = "rake"
            else:
                totals["eavesFt"] += length
                edge_type = "eave"

        edges.append({
            "type": edge_type,
            "facetIds": [p["facetId"] for p in parts],
            "lengthFt": round(length, 1),
            "aXYFt": [round(a[0], 2), round(a[1], 2)],
            "bXYFt": [round(b[0], 2), round(b[1], 2)],
        })

    totals["dripEdgeFt"] = totals["rakesFt"] + totals["eavesFt"]

    # Use polygon-derived values only if they look complete. Otherwise keep existing calibrated totals.
    lm = measurement.setdefault("lineMeasurements", {})
    for k, v in totals.items():
        if v > 0:
            lm[k] = round(v, 1)

    measurement["edges"] = edges
    measurement["diagramGeometry"] = {
        "status": "ok",
        "source": "google_solar_data_layers",
        "centerLat": lat0,
        "centerLon": lon0,
        "facets": diagram_facets,
        "edges": edges,
        "lineTotals": {k: round(v, 1) for k, v in totals.items()},
    }
    measurement["diagramGeometryStatus"] = "ok"
    measurement.setdefault("measurementNotes", []).append(
        "True roof diagram geometry generated from Google Solar Data Layers facet polygons."
    )
    return measurement
