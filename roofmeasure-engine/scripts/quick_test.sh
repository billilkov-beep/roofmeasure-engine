#!/bin/bash
# Quick one-line test for any address — runs measure_hybrid, prints headline numbers.
#
# Usage:
#   bash quick_test.sh "1404 Wedgewood Dr Cleburne TX 76033"
#   bash quick_test.sh 32.3347 -97.4153
#
# Prints one line per result. Use to spot-check after a deploy or to test
# a new customer-supplied address quickly.

if [ $# -lt 1 ]; then
    echo "Usage: $0 <address>     OR     $0 <lat> <lon>"
    exit 1
fi
if [ $# -ge 2 ]; then
    LAT="$1"; LON="$2"; ADDR="$LAT,$LON"
else
    ADDR="$1"
fi

sudo -E /home/roofmeasure/engine/venv/bin/python << PYEOF
import sys, os, time, requests, math
sys.path.insert(0, "/home/roofmeasure/engine")

ADDR = "$ADDR"
# Resolve lat/lon
if "," in ADDR and len(ADDR.split(",")) == 2:
    try:
        parts = [p.strip() for p in ADDR.split(",")]
        LAT, LON = float(parts[0]), float(parts[1])
    except ValueError:
        LAT = LON = None
else:
    LAT = LON = None
if LAT is None:
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params={"q": ADDR, "format": "json", "limit": 1},
                     headers={"User-Agent": "RoofMeasure/3.12"}, timeout=15)
    data = r.json()
    if not data: print(f"GEOCODE FAILED for {ADDR}"); sys.exit(1)
    LAT = float(data[0]["lat"]); LON = float(data[0]["lon"])

from roofmeasure.hybrid_pipeline import measure_hybrid
t = time.time()
r = measure_hybrid(LAT, LON)
elapsed = time.time() - t

if r.success:
    print(f"OK  ({elapsed:.1f}s)  src={r.primary_source}")
    print(f"  Address: {ADDR}")
    print(f"  Lat/Lon: ({LAT:.6f}, {LON:.6f})")
    print(f"  Area:    {r.total_area_m2 * 10.7639:.0f} sqft  ({r.total_area_m2:.1f} m²)")
    print(f"  Pitch:   {r.predominant_pitch_x_in_12:.1f}/12  ({r.predominant_pitch_deg:.1f}°)")
    print(f"  Facets:  {len(r.facets)}")
    for f in r.facets[:6]:
        print(f"    #{f.id}: {f.area_m2 * 10.7639:5.0f}sqft  pitch={f.pitch_x_in_12:.1f}/12  az={f.azimuth_deg:.0f}°  ({f.provenance})")
else:
    print(f"FAIL ({elapsed:.1f}s)  src={r.primary_source}")
    print(f"  Address: {ADDR}")
    print(f"  Lat/Lon: ({LAT:.6f}, {LON:.6f})")
    print(f"  Notes:")
    for n in r.notes[-8:]:
        print(f"    {n}")
PYEOF
