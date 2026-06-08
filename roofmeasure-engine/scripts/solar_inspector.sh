#!/bin/bash
# Solar API deep-dive — hits the Solar API at all 3 quality levels and dumps:
#   - Raw JSON responses (HIGH/MEDIUM/LOW)
#   - Parsed roof segments (facets) per quality level
#   - Building outline polygon if present
#   - Comparison to ground_truth.csv if address matches
#
# Usage:
#   bash solar_inspector.sh "1404 Wedgewood Dr Cleburne TX 76033"
#   bash solar_inspector.sh 32.3347 -97.4153
#
# Output: /tmp/v3_solar_debug_<slug>/

if [ $# -lt 1 ]; then
    echo "Usage: $0 <address>     OR     $0 <lat> <lon>"
    exit 1
fi
if [ $# -ge 2 ]; then
    LAT="$1"; LON="$2"; SLUG="latlon_${LAT}_${LON}"; ADDR="$LAT,$LON"
else
    ADDR="$1"
    SLUG=$(echo "$ADDR" | tr ' ,' '__' | tr -dc '[:alnum:]_-' | head -c 40)
fi
OUT=/tmp/v3_solar_debug_$SLUG
mkdir -p "$OUT"
echo "Dumping Solar API output to $OUT/"

sudo -E /home/roofmeasure/engine/venv/bin/python << PYEOF
import sys, os, csv, json, math, time, requests
sys.path.insert(0, "/home/roofmeasure/engine")
from pathlib import Path

OUT = Path("$OUT")
ADDR = "$ADDR"

# Resolve lat/lon
if "," in ADDR and len(ADDR.split(",")) == 2:
    try:
        parts = [p.strip() for p in ADDR.split(",")]
        LAT, LON = float(parts[0]), float(parts[1])
        address_text = f"({LAT}, {LON})"
    except ValueError:
        LAT = LON = None
else:
    LAT = LON = None
if LAT is None:
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params={"q": ADDR, "format": "json", "limit": 1},
                     headers={"User-Agent": "RoofMeasure/3.12"}, timeout=15)
    data = r.json()
    if not data: print("Geocoding failed"); sys.exit(1)
    LAT = float(data[0]["lat"]); LON = float(data[0]["lon"])
    address_text = ADDR

print(f"\n=== Solar deep-dive: {address_text} → ({LAT}, {LON}) ===\n")

KEY = os.environ.get("GOOGLE_SOLAR_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
if not KEY:
    print("NO API KEY in env. Set GOOGLE_SOLAR_API_KEY first.")
    sys.exit(1)

print(f"Key starts with {KEY[:6]}...\n")

# Hit all 3 quality levels
results = {}
for quality in ("HIGH", "MEDIUM", "LOW"):
    print(f"--- Quality: {quality} ---")
    t = time.time()
    r = requests.get(
        "https://solar.googleapis.com/v1/buildingInsights:findClosest",
        params={"location.latitude": LAT, "location.longitude": LON,
                "requiredQuality": quality, "key": KEY},
        timeout=30,
    )
    elapsed = time.time() - t
    print(f"  HTTP {r.status_code} ({elapsed:.1f}s)")
    if r.status_code == 200:
        data = r.json()
        results[quality] = data
        (OUT / f"raw_{quality}.json").write_text(json.dumps(data, indent=2))
        sp = data.get("solarPotential") or {}
        segs = sp.get("roofSegmentStats") or []
        total_m2 = sum((s.get("stats") or {}).get("areaMeters2", 0) for s in segs)
        print(f"  {len(segs)} segments, total {total_m2:.1f}m² ({total_m2*10.7639:.0f}sqft)")
        if segs:
            print(f"  per-segment:")
            for i, s in enumerate(segs):
                area = (s.get("stats") or {}).get("areaMeters2", 0)
                pd = s.get("pitchDegrees", 0)
                az = s.get("azimuthDegrees", 0)
                print(f"    #{i}: {area*10.7639:5.0f}sqft  pitch={pd:.1f}deg ({math.tan(math.radians(pd))*12:.1f}/12)  az={az:.0f}deg")
        # Building outline summary
        ob = data.get("boundingBox")
        ic = data.get("imageryProcessedDate")
        ia = data.get("imageryDate")
        print(f"  imagery date: {ia} (processed: {ic})")
        if ob:
            print(f"  bounding box: {ob}")
    else:
        print(f"  body: {r.text[:200]}")
    print()

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
        print(f"=== EagleView ground truth: {matched['address']} ===")
        print(f"  GT area:   {matched['gt_total_sqft']} sqft")
        print(f"  GT facets: {matched['gt_num_facets']}")
        print(f"  GT pitch:  {matched['gt_pitch_x12']}/12")
        print()
        # For each quality level, show how badly Solar disagrees
        for q in ("HIGH", "MEDIUM", "LOW"):
            if q in results:
                sp = results[q].get("solarPotential") or {}
                segs = sp.get("roofSegmentStats") or []
                total_m2 = sum((s.get("stats") or {}).get("areaMeters2", 0) for s in segs)
                gt_m2 = float(matched["gt_total_sqft"]) / 10.7639
                pct = (total_m2 - gt_m2) / gt_m2 * 100 if gt_m2 else 0
                print(f"  {q}: {len(segs)} facets vs gt {matched['gt_num_facets']}, "
                      f"{total_m2*10.7639:.0f} sqft vs gt {matched['gt_total_sqft']} ({pct:+.1f}%)")

print(f"\n=== Files saved to {OUT}/ ===")
for p in sorted(OUT.iterdir()):
    print(f"  {p.name}  ({p.stat().st_size} bytes)")
PYEOF
