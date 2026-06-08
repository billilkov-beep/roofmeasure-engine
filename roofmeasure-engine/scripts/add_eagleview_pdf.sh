#!/bin/bash
# Extract ground-truth fields from an EagleView PDF and append to ground_truth.csv.
#
# Usage:
#   bash add_eagleview_pdf.sh /path/to/report.pdf
#
# What it does:
#   1. pdftotext the PDF
#   2. Regex out the standard EagleView measurement block
#   3. Geocode the address via Nominatim
#   4. Append a row to /home/roofmeasure/engine/tests/ground_truth.csv
#   5. Print the new row + show the harness needs a re-run
#
# Tested on EagleView "Premium Report" format (sample reports from 2017-2021).

if [ $# -lt 1 ]; then
    echo "Usage: $0 <eagleview.pdf>"
    exit 1
fi
PDF="$1"
if [ ! -f "$PDF" ]; then
    echo "PDF not found: $PDF"
    exit 1
fi

# Ensure pdftotext is available
command -v pdftotext > /dev/null 2>&1 || sudo apt-get install -y poppler-utils 2>&1 | tail -1

sudo /home/roofmeasure/engine/venv/bin/python << PYEOF
import csv, re, subprocess, sys, time
from pathlib import Path
import requests

PDF = Path("$PDF")
GT_CSV = Path("/home/roofmeasure/engine/tests/ground_truth.csv")

# 1. Extract text
text = subprocess.check_output(["pdftotext", "-layout", str(PDF), "-"], text=True)

# 2. Regex out fields
def pick(pattern, default=""):
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else default

# Address: first line before "Report:" — handles US (TX 76033) and Canadian (ON K0H1V0) formats
# Examples seen:
#   "1404 Wedgewood Dr, Cleburne, TX 76033-4514"  (US)
#   "3229 Forest Rd, South Frontenac, ON K0H1V0"   (Canada)
US_ADDR_RE = r"^\s*(\d+\s+[A-Za-z][^\n]*?(?:[A-Z]{2}\s*\d{5}(?:-\d{4})?))"
CA_ADDR_RE = r"^\s*(\d+\s+[A-Za-z][^\n]*?(?:[A-Z]{2}\s*[A-Z]\d[A-Z]\s*\d[A-Z]\d))"
addr_match = re.search(US_ADDR_RE, text, re.MULTILINE) or re.search(CA_ADDR_RE, text, re.MULTILINE)
address = addr_match.group(1).strip() if addr_match else ""

# Strip any commas (we'll re-add for the CSV)
address_clean = re.sub(r",\s*", " ", address)
# Strip ZIP+4 suffix
address_clean = re.sub(r"(\d{5})-\d{4}", r"\1", address_clean)

report_id = pick(r"Report:?\s*(\d{6,10})")

# US reports use "Total Roof Area", Canadian reports use "Total Area"
total_sqft = (pick(r"Total Roof Area\s*=\s*([\d,]+)\s*sq\s*ft") or
              pick(r"Total Area\s*=\s*([\d,]+)\s*sq\s*ft"))
total_sqft = total_sqft.replace(",", "") if total_sqft else ""

facets = pick(r"Total Roof Facets\s*=\s*(\d+)")
pitch = pick(r"Predominant Pitch\s*=\s*(\d+)/12")
ridges = pick(r"Total Ridges/?Hips\s*=\s*([\d,]+)\s*ft")
ridges = ridges.replace(",", "") if ridges else ""
# Some reports have separate Ridges and Hips
if not ridges:
    r1 = pick(r"Total Ridges\s*=\s*([\d,]+)\s*ft").replace(",", "") or "0"
    r2 = pick(r"Total Hips\s*=\s*([\d,]+)\s*ft").replace(",", "") or "0"
    try: ridges = str(int(r1) + int(r2))
    except: ridges = r1

valleys = pick(r"Total Valleys\s*=\s*([\d,]+)\s*ft").replace(",", "")
rakes = pick(r"Total Rakes\s*=\s*([\d,]+)\s*ft").replace(",", "")
eaves = pick(r"Total Eaves\s*=\s*([\d,]+)\s*ft").replace(",", "")

date = pick(r"(\d{1,2}/\d{1,2}/\d{4})")
# Convert M/D/YYYY -> YYYY-MM-DD
if date:
    parts = date.split("/")
    if len(parts) == 3:
        date = f"{parts[2]}-{int(parts[0]):02d}-{int(parts[1]):02d}"

# Country guess from address
import re as _re
country = "CA" if _re.search(r"\b(BC|ON|QC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)\b\s*\w?\d", address) else "US"

# Predominant pitch -> degrees
import math
pitch_x12 = int(pitch) if pitch else 0
pitch_deg = round(math.degrees(math.atan(pitch_x12 / 12)), 1) if pitch_x12 else 0

print(f"Parsed from {PDF.name}:")
print(f"  address:     {address_clean}")
print(f"  report_id:   {report_id}")
print(f"  total_sqft:  {total_sqft}")
print(f"  facets:      {facets}")
print(f"  pitch:       {pitch_x12}/12 ({pitch_deg} deg)")
print(f"  ridges/hips: {ridges} ft")
print(f"  valleys:     {valleys} ft")
print(f"  rakes:       {rakes} ft")
print(f"  eaves:       {eaves} ft")
print(f"  country:     {country}")
print(f"  date:        {date}")

if not (address_clean and total_sqft and facets):
    print("\nMissing required fields. Aborting.")
    print("(Inspect the PDF text manually — EagleView format may have changed.)")
    sys.exit(1)

# 3. Geocode via Nominatim
print(f"\nGeocoding via Nominatim...")
r = requests.get(
    "https://nominatim.openstreetmap.org/search",
    params={"q": address_clean, "format": "json", "limit": 1},
    headers={"User-Agent": "RoofMeasure/3.12 (https://roofmeasure.canadasroofer.com)"},
    timeout=15,
)
if r.status_code != 200 or not r.json():
    print(f"  geocoding failed ({r.status_code})")
    lat, lon = "", ""
else:
    lat = float(r.json()[0]["lat"])
    lon = float(r.json()[0]["lon"])
    print(f"  lat={lat:.6f}, lon={lon:.6f}")
    time.sleep(1.1)  # respect Nominatim 1 req/sec

# 4. Append to ground_truth.csv
if not GT_CSV.exists():
    print(f"\nground_truth.csv not found at {GT_CSV} — creating with header")
    with open(GT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["address","lat","lon","gt_total_sqft","gt_num_facets","gt_pitch_x12",
                    "gt_predominant_pitch_deg","gt_ridges_hips_ft","gt_valleys_ft",
                    "gt_rakes_ft","gt_eaves_ft","country","eagleview_report_id","eagleview_date"])

# Check if this report_id is already present
existing = []
with open(GT_CSV) as f:
    reader = csv.DictReader(f); fieldnames = reader.fieldnames
    existing = list(reader)
if report_id and any(r.get("eagleview_report_id") == report_id for r in existing):
    print(f"\nReport {report_id} already in ground_truth.csv. Skipping append.")
    sys.exit(0)

row = {
    "address": address_clean, "lat": lat, "lon": lon,
    "gt_total_sqft": total_sqft, "gt_num_facets": facets,
    "gt_pitch_x12": pitch_x12, "gt_predominant_pitch_deg": pitch_deg,
    "gt_ridges_hips_ft": ridges or "0",
    "gt_valleys_ft": valleys or "0",
    "gt_rakes_ft": rakes or "0",
    "gt_eaves_ft": eaves or "0",
    "country": country,
    "eagleview_report_id": report_id,
    "eagleview_date": date,
}
with open(GT_CSV, "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writerow(row)

print(f"\nAppended to {GT_CSV}")
print(f"  Total rows now: {len(existing) + 1}")
print()
print("The auto-test watcher (if running) will NOT re-trigger on CSV changes —")
print("Touch a .py file to force a re-run, or run the harness manually:")
print("  sudo -E /home/roofmeasure/engine/venv/bin/python \\")
print("       /home/roofmeasure/engine/tests/ground_truth_harness.py \\")
print(f"       {GT_CSV}")
PYEOF
