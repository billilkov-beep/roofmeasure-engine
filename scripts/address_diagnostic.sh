#!/bin/bash
# Per-address diagnostic dump — run on any address (lat/lon or street).
#
# Usage:
#   bash address_diagnostic.sh "1404 Wedgewood Dr Cleburne TX 76033"
#   bash address_diagnostic.sh 32.3347 -97.4153
#
# Dumps everything we know about that address into /tmp/v3_debug_<slug>/:
#   - geocoding_result.json (Nominatim + alternates)
#   - footprint.geojson (OSM polygon)
#   - footprint_stats.json (vertex count, area, source)
#   - lidar_meta.json (tile info, point count, density, z-unit)
#   - lidar_points.csv (first 1000 raw LIDAR points)
#   - segmentation.json (facets + edges)
#   - hybrid_result.json (final MeasurementResult)
#   - eagleview_comparison.json (if address is in ground_truth.csv)

if [ $# -lt 1 ]; then
    echo "Usage: $0 <address>     OR     $0 <lat> <lon>"
    exit 1
fi

if [ $# -ge 2 ]; then
    LAT="$1"
    LON="$2"
    SLUG="latlon_${LAT}_${LON}"
    ADDR="$LAT,$LON"
else
    ADDR="$1"
    SLUG=$(echo "$ADDR" | tr ' ,' '__' | tr -dc '[:alnum:]_-' | head -c 40)
fi

OUT=/tmp/v3_debug_$SLUG
mkdir -p "$OUT"
echo "Dumping diagnostics to $OUT/"

sudo -E /home/roofmeasure/engine/venv/bin/python << PYEOF
import csv, json, logging, sys, time, requests
from pathlib import Path
from dataclasses import asdict, is_dataclass

sys.path.insert(0, "/home/roofmeasure/engine")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("diag")

OUT = Path("$OUT")
ADDR = "$ADDR"

# --------------------------------------------------------------------------
# Resolve lat/lon
# --------------------------------------------------------------------------
if "," in ADDR and len(ADDR.split(",")) == 2 and all(
    p.replace("-", "").replace(".", "").strip().replace(" ", "").isdigit()
    for p in ADDR.split(",")
):
    parts = [p.strip() for p in ADDR.split(",")]
    LAT, LON = float(parts[0]), float(parts[1])
    address_text = f"({LAT}, {LON})"
else:
    UA = "RoofMeasureEngine/3.5 (https://roofmeasure.canadasroofer.com)"
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": ADDR, "format": "json", "limit": 3, "addressdetails": 1},
        headers={"User-Agent": UA}, timeout=15,
    )
    data = r.json()
    (OUT / "geocoding_result.json").write_text(json.dumps(data, indent=2))
    if not data:
        print("Geocoding failed.")
        sys.exit(1)
    LAT, LON = float(data[0]["lat"]), float(data[0]["lon"])
    address_text = ADDR

print(f"\n=== Address: {address_text}, lat={LAT}, lon={LON} ===")

# --------------------------------------------------------------------------
# Footprint
# --------------------------------------------------------------------------
print("\n--- footprint_v2 ---")
from roofmeasure.footprint_v2 import get_building_footprint, polygon_area_m2
fp = get_building_footprint(LAT, LON)
if fp is None:
    (OUT / "footprint_stats.json").write_text(json.dumps({"status": "NOT FOUND"}, indent=2))
    print("NO FOOTPRINT")
else:
    area = polygon_area_m2(fp.polygon_lonlat)
    stats = {
        "source": fp.source,
        "vertex_count": len(fp.polygon_lonlat),
        "area_m2": round(area, 2),
        "area_sqft": round(area * 10.7639, 0),
        "centroid_lonlat": list(fp.centroid_lonlat),
        "osm_id": str(fp.osm_id) if fp.osm_id else None,
    }
    (OUT / "footprint_stats.json").write_text(json.dumps(stats, indent=2))
    geo = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[lo, la] for lo, la in fp.polygon_lonlat]],
        },
        "properties": stats,
    }
    (OUT / "footprint.geojson").write_text(json.dumps(geo, indent=2))
    print(f"  source={fp.source}, verts={len(fp.polygon_lonlat)}, area={area:.0f}m^2 ({area*10.7639:.0f}sqft)")

# --------------------------------------------------------------------------
# LIDAR
# --------------------------------------------------------------------------
print("\n--- lidar_v2_raw ---")
if fp is None:
    print("  skipped (no footprint)")
    crop = None
else:
    from roofmeasure.lidar_v2_raw import fetch_lidar_for_footprint
    t = time.time()
    crop = fetch_lidar_for_footprint(fp)
    elapsed = time.time() - t
    if crop is None:
        (OUT / "lidar_meta.json").write_text(json.dumps({"status": "NO LIDAR", "elapsed_s": elapsed}, indent=2))
        print(f"  NO LIDAR ({elapsed:.1f}s)")
    else:
        meta = {
            "source": crop.source,
            "source_tile": crop.source_tile,
            "point_count": len(crop.points_local_m),
            "density_per_m2": round(crop.point_density_per_m2 or 0, 2),
            "captured_year": crop.captured_year,
            "z_unit_detected": crop.z_unit_detected,
            "classifications_used": crop.classifications_used,
            "raw_point_count_in_tile": crop.raw_point_count,
            "elapsed_s": round(elapsed, 1),
        }
        (OUT / "lidar_meta.json").write_text(json.dumps(meta, indent=2))
        with open(OUT / "lidar_points.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["east_m", "north_m", "elev_m"])
            for p in crop.points_local_m[:1000]:
                w.writerow([f"{p[0]:.3f}", f"{p[1]:.3f}", f"{p[2]:.3f}"])
        print(f"  source={crop.source}, n={len(crop.points_local_m)}, density={crop.point_density_per_m2:.2f}/m^2")

# --------------------------------------------------------------------------
# Segmentation
# --------------------------------------------------------------------------
print("\n--- segmentation_v2 ---")
if crop is None:
    print("  skipped (no LIDAR)")
    seg = None
else:
    from roofmeasure.segmentation_v2 import segment_roof
    try:
        seg = segment_roof(
            crop.points_local_m,
            density_hint=crop.point_density_per_m2,
            points_already_filtered=crop.classifications_used,
            footprint_area_m2=polygon_area_m2(fp.polygon_lonlat),
            footprint_vertex_count=len(fp.polygon_lonlat),
        )
    except TypeError:
        # v3.4 fallback (no footprint_vertex_count)
        seg = segment_roof(
            crop.points_local_m,
            density_hint=crop.point_density_per_m2,
            points_already_filtered=crop.classifications_used,
            footprint_area_m2=polygon_area_m2(fp.polygon_lonlat),
        )
    facets_out = []
    for f in seg.facets:
        facets_out.append({
            "id": f.id,
            "area_m2": round(f.area_m2, 2),
            "area_sqft": round(f.area_m2 * 10.7639, 0),
            "pitch_x12": round(f.pitch_x_in_12, 1),
            "pitch_deg": round(f.pitch_deg, 1),
            "azimuth_deg": round(f.azimuth_deg, 0),
            "inlier_count": len(f.inlier_indices),
        })
    seg_dump = {
        "facets": facets_out,
        "ground_z": round(seg.ground_z, 2),
        "point_count": seg.point_count,
        "total_area_m2": round(sum(f.area_m2 for f in seg.facets), 2),
        "total_area_sqft": round(sum(f.area_m2 for f in seg.facets) * 10.7639, 0),
        "notes": seg.notes,
    }
    (OUT / "segmentation.json").write_text(json.dumps(seg_dump, indent=2))
    print(f"  {len(seg.facets)} facets, total_area={seg_dump['total_area_sqft']}sqft")

# --------------------------------------------------------------------------
# Hybrid pipeline
# --------------------------------------------------------------------------
print("\n--- hybrid_pipeline ---")
from roofmeasure.hybrid_pipeline import measure_hybrid
hr = measure_hybrid(LAT, LON)
hr_dump = {
    "success": hr.success,
    "primary_source": hr.primary_source,
    "total_area_m2": round(hr.total_area_m2, 2),
    "total_area_sqft": round(hr.total_area_m2 * 10.7639, 0),
    "predominant_pitch_x12": round(hr.predominant_pitch_x_in_12, 1),
    "facet_count": len(hr.facets),
    "duration_s": round(hr.duration_s, 1),
    "notes": hr.notes,
}
(OUT / "hybrid_result.json").write_text(json.dumps(hr_dump, indent=2))
print(f"  success={hr.success}, source={hr.primary_source}, "
      f"area={hr_dump['total_area_sqft']}sqft, facets={len(hr.facets)}")

# --------------------------------------------------------------------------
# EagleView ground-truth comparison
# --------------------------------------------------------------------------
print("\n--- ground-truth comparison ---")
GT = Path("/home/roofmeasure/engine/tests/ground_truth.csv")
if GT.exists():
    with open(GT) as f:
        rows = list(csv.DictReader(f))
    addr_lower = ADDR.lower().split(",")[0].strip()
    matched = None
    for r in rows:
        if r["address"].lower() in addr_lower or addr_lower in r["address"].lower():
            matched = r
            break
    if matched:
        try:
            gt_sqft = float(matched["gt_total_sqft"])
        except Exception:
            gt_sqft = 0
        try:
            eng_sqft = hr.total_area_m2 * 10.7639
        except Exception:
            eng_sqft = 0
        diff_pct = ((eng_sqft - gt_sqft) / gt_sqft * 100) if gt_sqft else 0
        comp = {
            "matched_address": matched["address"],
            "gt_total_sqft": gt_sqft,
            "engine_total_sqft": round(eng_sqft, 0),
            "delta_pct": round(diff_pct, 1),
            "gt_facets": int(matched.get("gt_num_facets") or 0),
            "engine_facets": len(hr.facets),
            "gt_pitch_x12": float(matched.get("gt_pitch_x12") or 0),
            "engine_pitch_x12": round(hr.predominant_pitch_x_in_12, 1),
            "gt_ridges_hips_ft": float(matched.get("gt_ridges_hips_ft") or 0),
            "gt_valleys_ft": float(matched.get("gt_valleys_ft") or 0),
            "gt_rakes_ft": float(matched.get("gt_rakes_ft") or 0),
            "gt_eaves_ft": float(matched.get("gt_eaves_ft") or 0),
        }
        (OUT / "eagleview_comparison.json").write_text(json.dumps(comp, indent=2))
        print(f"  gt={gt_sqft}sqft engine={eng_sqft:.0f}sqft Δ={diff_pct:+.1f}%")
    else:
        print(f"  no match in {GT}")
else:
    print(f"  {GT} not found")

print(f"\nAll outputs in {OUT}/:")
for p in sorted(OUT.iterdir()):
    print(f"  {p.name} ({p.stat().st_size} bytes)")
PYEOF
