#!/bin/bash
# LIDAR inspector — deep-dive on a single address.
#
# Dumps everything we have about the LIDAR data for one building:
#   - Footprint polygon (GeoJSON)
#   - Raw LIDAR points within bbox (CSV)
#   - Roof points after ground filter (CSV)
#   - Per-point computed normal (CSV)
#   - Pitch histogram (CSV — count per 1-degree bucket)
#   - 3D scatter PNG (matplotlib) of the point cloud colored by pitch
#   - Comparison to ground truth from ground_truth.csv if address matches
#
# Usage:
#   bash lidar_inspector.sh "624 Merrill Dr Bedford TX 76022"
#   bash lidar_inspector.sh 32.8301 -97.1537
#
# Output: /tmp/v3_lidar_debug_<slug>/
# After running, scp the PNG to your machine to view:
#   scp root@roofmeasure.canadasroofer.com:/tmp/v3_lidar_debug_*/scatter_3d.png .

if [ $# -lt 1 ]; then
    echo "Usage: $0 <address>     OR     $0 <lat> <lon>"
    exit 1
fi

if [ $# -ge 2 ]; then
    LAT="$1"; LON="$2"
    SLUG="latlon_${LAT}_${LON}"
    ADDR="$LAT,$LON"
else
    ADDR="$1"
    SLUG=$(echo "$ADDR" | tr ' ,' '__' | tr -dc '[:alnum:]_-' | head -c 40)
fi
OUT=/tmp/v3_lidar_debug_$SLUG
mkdir -p "$OUT"
echo "Dumping to $OUT/"

# Verify matplotlib is available
sudo /home/roofmeasure/engine/venv/bin/python -c "import matplotlib" 2>/dev/null || \
    sudo /home/roofmeasure/engine/venv/bin/pip install matplotlib --quiet --break-system-packages

sudo -E /home/roofmeasure/engine/venv/bin/python << PYEOF
import sys, os, csv, json, math, logging, time, requests
sys.path.insert(0, "/home/roofmeasure/engine")
from pathlib import Path
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("lidar_inspect")
OUT = Path("$OUT")
ADDR = "$ADDR"

# Resolve lat/lon
if "," in ADDR and len(ADDR.split(",")) == 2:
    parts = [p.strip() for p in ADDR.split(",")]
    try:
        LAT = float(parts[0]); LON = float(parts[1])
        address_text = f"({LAT}, {LON})"
    except ValueError:
        LAT = LON = None
else:
    LAT = LON = None
if LAT is None:
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": ADDR, "format": "json", "limit": 1},
        headers={"User-Agent": "RoofMeasure/3.11"}, timeout=15,
    )
    data = r.json()
    if not data: print("Geocoding failed."); sys.exit(1)
    LAT = float(data[0]["lat"]); LON = float(data[0]["lon"])
    address_text = ADDR
print(f"\n=== Inspecting {address_text} → ({LAT}, {LON}) ===\n")

# Footprint
from roofmeasure.footprint_v2 import get_building_footprint, polygon_area_m2
fp = get_building_footprint(LAT, LON)
if fp is None:
    print("NO FOOTPRINT — cannot proceed with LIDAR inspection without bbox")
    sys.exit(1)
fp_area = polygon_area_m2(fp.polygon_lonlat)
print(f"Footprint: {fp.source}, {len(fp.polygon_lonlat)} verts, area={fp_area:.0f}m^2")
(OUT / "footprint.geojson").write_text(json.dumps({
    "type": "Feature",
    "geometry": {"type": "Polygon", "coordinates": [[[lo, la] for lo, la in fp.polygon_lonlat]]},
    "properties": {"source": fp.source, "verts": len(fp.polygon_lonlat), "area_m2": fp_area},
}, indent=2))

# Fetch LIDAR (uses cache)
from roofmeasure.lidar_v2_raw import fetch_lidar_for_footprint
t = time.time()
crop = fetch_lidar_for_footprint(fp)
print(f"LIDAR fetch: {time.time()-t:.1f}s")
if crop is None:
    print("NO LIDAR for this address (Canadian or out-of-coverage). Try NRCan instead.")
    sys.exit(1)

pts = crop.points_local_m
print(f"  source={crop.source}, n={len(pts)}, density={crop.point_density_per_m2:.2f}/m^2, z_unit={crop.z_unit_detected}")
print(f"  z range: {pts[:,2].min():.2f}m to {pts[:,2].max():.2f}m (span {pts[:,2].max()-pts[:,2].min():.2f}m)")

# Save raw points
with open(OUT / "raw_points.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["east_m", "north_m", "elev_m"])
    for p in pts: w.writerow([f"{p[0]:.3f}", f"{p[1]:.3f}", f"{p[2]:.3f}"])

# Ground filter
z = pts[:, 2]
ground_z = float(np.percentile(z, 1.0))
roof_mask = z > (ground_z + 1.5)
roof_pts = pts[roof_mask]
print(f"\nGround filter: ground_z={ground_z:.2f}m, kept {len(roof_pts)}/{len(pts)} above 1.5m")

with open(OUT / "roof_points.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["east_m", "north_m", "elev_m"])
    for p in roof_pts: w.writerow([f"{p[0]:.3f}", f"{p[1]:.3f}", f"{p[2]:.3f}"])

# Compute per-point normals via Open3D
import open3d as o3d
pcd = o3d.geometry.PointCloud()
pcd.points = o3d.utility.Vector3dVector(roof_pts)
pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=2.0, max_nn=30))
pcd.orient_normals_to_align_with_direction(orientation_reference=[0, 0, 1])
normals = np.asarray(pcd.normals)

# Compute per-point pitch (angle between normal and vertical)
nz = np.abs(normals[:, 2])  # ensure upward
pitches_deg = np.degrees(np.arccos(np.clip(nz, -1, 1)))
pitches_x12 = np.tan(np.radians(pitches_deg)) * 12

# Histogram
print(f"\nPitch histogram (degrees, count per 5-deg bucket):")
print(f"{'pitch_deg':>10} {'pitch_x12':>10} {'count':>8} {'pct':>6}")
bins = np.arange(0, 91, 5)
hist, edges = np.histogram(pitches_deg, bins=bins)
total = sum(hist)
for i, c in enumerate(hist):
    if c == 0: continue
    lo, hi = edges[i], edges[i+1]
    pitch_x12_mid = np.tan(np.radians((lo+hi)/2)) * 12
    pct = c/total*100
    bar = "#" * int(pct/2)
    print(f"  {lo:3d}-{hi:3d}  {pitch_x12_mid:5.1f}/12  {c:8d}  {pct:5.1f}%  {bar}")

# Save pitch histogram CSV
with open(OUT / "pitch_histogram.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["pitch_deg_low", "pitch_deg_high", "pitch_x12_midpoint", "count", "pct"])
    for i, c in enumerate(hist):
        lo, hi = edges[i], edges[i+1]
        w.writerow([lo, hi, round(np.tan(np.radians((lo+hi)/2))*12, 2), c, round(c/total*100, 2)])

# Compute median and 90th percentile pitches
median_pitch = float(np.percentile(pitches_deg, 50))
p90_pitch = float(np.percentile(pitches_deg, 90))
print(f"\n  Median pitch: {median_pitch:.1f}deg ({np.tan(np.radians(median_pitch))*12:.1f}/12)")
print(f"  90th pct pitch: {p90_pitch:.1f}deg ({np.tan(np.radians(p90_pitch))*12:.1f}/12)")

# Per-point CSV with normals
with open(OUT / "roof_points_with_normals.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["east_m", "north_m", "elev_m", "nx", "ny", "nz", "pitch_deg", "pitch_x12"])
    for p, n, pd, px in zip(roof_pts, normals, pitches_deg, pitches_x12):
        w.writerow([f"{p[0]:.3f}", f"{p[1]:.3f}", f"{p[2]:.3f}",
                    f"{n[0]:.4f}", f"{n[1]:.4f}", f"{n[2]:.4f}",
                    f"{pd:.1f}", f"{px:.1f}"])

# Render 3D scatter PNG colored by pitch
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa

fig = plt.figure(figsize=(12, 9))
ax = fig.add_subplot(111, projection="3d")
sc = ax.scatter(roof_pts[:, 0], roof_pts[:, 1], roof_pts[:, 2],
                c=pitches_deg, cmap="viridis", s=4, vmin=0, vmax=60)
plt.colorbar(sc, label="Pitch (degrees, 0=flat 45=12/12)")
ax.set_xlabel("East (m)"); ax.set_ylabel("North (m)"); ax.set_zlabel("Elev (m)")
ax.set_title(f"{address_text}\n{len(roof_pts)} pts, density={crop.point_density_per_m2:.1f}/m^2\nmedian pitch={median_pitch:.1f}deg ({np.tan(np.radians(median_pitch))*12:.1f}/12)")
plt.tight_layout()
plt.savefig(OUT / "scatter_3d.png", dpi=100)
print(f"\n3D scatter saved to {OUT}/scatter_3d.png")

# Top-down view
fig2 = plt.figure(figsize=(10, 10))
ax2 = fig2.add_subplot(111)
sc2 = ax2.scatter(roof_pts[:, 0], roof_pts[:, 1], c=pitches_deg, cmap="viridis", s=8, vmin=0, vmax=60)
plt.colorbar(sc2, label="Pitch (degrees)")
ax2.set_xlabel("East (m)"); ax2.set_ylabel("North (m)")
ax2.set_aspect("equal")
ax2.set_title(f"{address_text} top-down — roof points colored by pitch")
plt.tight_layout()
plt.savefig(OUT / "topdown_2d.png", dpi=100)
print(f"Top-down view saved to {OUT}/topdown_2d.png")

# Compare to ground truth
GT = Path("/home/roofmeasure/engine/tests/ground_truth.csv")
if GT.exists():
    with open(GT) as f:
        rows = list(csv.DictReader(f))
    addr_lower = ADDR.lower().split(",")[0].strip()
    matched = None
    for r in rows:
        if r["address"].lower() in addr_lower or addr_lower in r["address"].lower():
            matched = r; break
    if matched:
        print(f"\n=== EagleView ground truth match: {matched['address']} ===")
        print(f"  GT area:    {matched['gt_total_sqft']} sqft ({float(matched['gt_total_sqft'])/10.7639:.1f}m^2)")
        print(f"  GT facets:  {matched['gt_num_facets']}")
        print(f"  GT pitch:   {matched['gt_pitch_x12']}/12 ({float(matched['gt_predominant_pitch_deg']):.1f}deg)")
        print(f"  GT ridges/hips: {matched.get('gt_ridges_hips_ft','?')} ft")
        print(f"  GT valleys: {matched.get('gt_valleys_ft','?')} ft")
        print(f"  GT rakes:   {matched.get('gt_rakes_ft','?')} ft")
        print(f"  GT eaves:   {matched.get('gt_eaves_ft','?')} ft")
        print()
        print(f"  Engine median pitch from LIDAR normals: {np.tan(np.radians(median_pitch))*12:.1f}/12")
        print(f"  Engine 90th pct pitch from LIDAR normals: {np.tan(np.radians(p90_pitch))*12:.1f}/12")
        print(f"\n  PITCH GAP: EagleView says {matched['gt_pitch_x12']}/12, LIDAR median says {np.tan(np.radians(median_pitch))*12:.1f}/12")

print(f"\n=== Files in {OUT}/ ===")
for p in sorted(OUT.iterdir()):
    print(f"  {p.name} ({p.stat().st_size} bytes)")
print(f"\nView the PNG: scp root@roofmeasure.canadasroofer.com:{OUT}/scatter_3d.png .")
PYEOF
