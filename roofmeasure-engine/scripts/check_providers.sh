#!/bin/bash
# Provider URL validator — pre-flight check for all external endpoints the
# engine uses. Reports which providers are responding and which are dead,
# so we can diagnose harness failures before re-running the full test.
#
# Paste in SSH any time. Takes ~30 seconds.

sudo /home/roofmeasure/engine/venv/bin/python << 'PYEOF'
import os, time, json, requests
from datetime import datetime

UA = "RoofMeasureEngine/3.11 (https://roofmeasure.canadasroofer.com)"
HEADERS = {"User-Agent": UA}
TIMEOUT = 10

# Bedford TX lat/lon as test point
TEST_LAT, TEST_LON = 32.8301, -97.1537

def section(name):
    print(f"\n{'=' * 50}")
    print(f"  {name}")
    print('=' * 50)

def check(name, fn):
    t = time.time()
    try:
        result, status = fn()
        elapsed = time.time() - t
        print(f"  {name}: {status}  ({elapsed:.1f}s)")
        if result is not None:
            print(f"    -> {result}")
        return True
    except Exception as e:
        elapsed = time.time() - t
        print(f"  {name}: ERR {e}  ({elapsed:.1f}s)")
        return False

# ============================================================================
section("1. USGS TNM Access API (for US LIDAR)")
def tnm():
    r = requests.get(
        "https://tnmaccess.nationalmap.gov/api/v1/products",
        params={"bbox": f"{TEST_LON-0.001},{TEST_LAT-0.001},{TEST_LON+0.001},{TEST_LAT+0.001}",
                "datasets": "Lidar Point Cloud (LPC)", "outputFormat": "JSON", "max": 1},
        headers=HEADERS, timeout=TIMEOUT,
    )
    n_items = len(r.json().get("items", [])) if r.status_code == 200 else 0
    return f"{n_items} LPC products for Bedford TX", f"HTTP {r.status_code}"
check("TNM Access", tnm)

# ============================================================================
section("2. USGS rockyweb LAZ download server")
def rockyweb():
    r = requests.head("https://rockyweb.usgs.gov/", headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    return None, f"HTTP {r.status_code}"
check("rockyweb root", rockyweb)

# ============================================================================
section("3. Overpass mirrors (for OSM building footprints)")
for mirror in [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]:
    name = mirror.split("//")[1].split("/")[0]
    def make_check(url=mirror):
        def _c():
            r = requests.post(url, data={"data": "[out:json];out 1;"},
                              headers=HEADERS, timeout=TIMEOUT)
            return f"response len={len(r.text)}", f"HTTP {r.status_code}"
        return _c
    check(name, make_check())

# ============================================================================
section("4. Nominatim geocoder")
def nominatim():
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": "Bedford TX", "format": "json", "limit": 1},
        headers=HEADERS, timeout=TIMEOUT,
    )
    n = len(r.json()) if r.status_code == 200 else 0
    return f"{n} results", f"HTTP {r.status_code}"
check("Nominatim", nominatim)

# ============================================================================
section("5. MS Planetary Computer STAC (Global ML Buildings)")
def pc_collections():
    r = requests.get(
        "https://planetarycomputer.microsoft.com/api/stac/v1/collections/ms-buildings",
        headers=HEADERS, timeout=TIMEOUT,
    )
    if r.status_code == 200:
        title = r.json().get("title", "?")
        return f"title='{title}'", f"HTTP {r.status_code}"
    return r.text[:80], f"HTTP {r.status_code}"
check("PC collections/ms-buildings", pc_collections)

def pc_search():
    r = requests.post(
        "https://planetarycomputer.microsoft.com/api/stac/v1/search",
        json={"collections": ["ms-buildings"],
              "intersects": {"type": "Point", "coordinates": [TEST_LON, TEST_LAT]},
              "limit": 1},
        headers=HEADERS, timeout=TIMEOUT,
    )
    if r.status_code == 200:
        n = len(r.json().get("features", []))
        return f"{n} features intersecting Bedford", f"HTTP {r.status_code}"
    return r.text[:80], f"HTTP {r.status_code}"
check("PC search Bedford", pc_search)

# ============================================================================
section("6. NRCan Datacube WCS (Canadian DEM)")
def nrcan_caps():
    r = requests.get(
        "https://datacube.services.geo.ca/ows/elevation",
        params={"service": "WCS", "version": "2.0.1", "request": "GetCapabilities"},
        headers=HEADERS, timeout=TIMEOUT,
    )
    if r.status_code == 200:
        # Extract coverage IDs
        import re
        ids = re.findall(r'CoverageId>([^<]+)</', r.text)
        return f"coverages: {ids[:6]}", f"HTTP {r.status_code}"
    return r.text[:80], f"HTTP {r.status_code}"
check("NRCan WCS Capabilities", nrcan_caps)

# Try GetCoverage for Bedford (out of Canada) - expect "out of coverage" error
def nrcan_coverage():
    r = requests.get(
        "https://datacube.services.geo.ca/ows/elevation",
        params=[("service","WCS"),("version","2.0.1"),("request","GetCoverage"),
                ("coverageId","dsm-1m"),
                ("subset",f"Lat({TEST_LAT-0.001},{TEST_LAT+0.001})"),
                ("subset",f"Long({TEST_LON-0.001},{TEST_LON+0.001})"),
                ("format","image/tiff")],
        headers=HEADERS, timeout=TIMEOUT,
    )
    ct = r.headers.get("Content-Type", "")
    return f"ct={ct} bytes={len(r.content)}", f"HTTP {r.status_code}"
check("NRCan dsm-1m for Bedford (US, expect error)", nrcan_coverage)

# Try for Whitby ON (in Canada)
def nrcan_whitby():
    r = requests.get(
        "https://datacube.services.geo.ca/ows/elevation",
        params=[("service","WCS"),("version","2.0.1"),("request","GetCoverage"),
                ("coverageId","dsm-1m"),
                ("subset","Lat(43.907,43.908)"),
                ("subset","Long(-78.972,-78.971)"),
                ("format","image/tiff")],
        headers=HEADERS, timeout=TIMEOUT,
    )
    ct = r.headers.get("Content-Type", "")
    return f"ct={ct} bytes={len(r.content)}", f"HTTP {r.status_code}"
check("NRCan dsm-1m for Whitby ON", nrcan_whitby)

# ============================================================================
section("7. Google Solar API")
def solar():
    key = os.environ.get("GOOGLE_SOLAR_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        return None, "NO KEY"
    r = requests.get(
        "https://solar.googleapis.com/v1/buildingInsights:findClosest",
        params={"location.latitude": TEST_LAT, "location.longitude": TEST_LON,
                "requiredQuality": "HIGH", "key": key},
        headers=HEADERS, timeout=TIMEOUT,
    )
    if r.status_code == 200:
        sp = r.json().get("solarPotential") or {}
        segs = sp.get("roofSegmentStats") or []
        return f"{len(segs)} segments for Bedford TX", f"HTTP {r.status_code}"
    return r.text[:100], f"HTTP {r.status_code}"
check("Solar API for Bedford (HIGH)", solar)

# ============================================================================
section("8. Microsoft Footprints (US/CA blob URLs)")
for label, url in [
    ("MS US v2 (old)", "https://usbuildingdata.blob.core.windows.net/usbuildings-v2/Texas.geojson.zip"),
    ("MS US (alt)", "https://usbuildings.blob.core.windows.net/usbuildings-v2/Texas.geojson.zip"),
    ("MS US Github", "https://github.com/microsoft/USBuildingFootprints/releases/download/v2.0/Texas.geojson.zip"),
    ("MS CA v2 (old)", "https://usbuildingdata.blob.core.windows.net/canadian-buildings-v2/Ontario.geojson.zip"),
]:
    def make(url=url):
        def _c():
            r = requests.head(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            return None, f"HTTP {r.status_code}"
        return _c
    check(label, make())

print("\n" + "=" * 50)
print("  Summary")
print("=" * 50)
print("Each 'HTTP 200' is a working provider.")
print("HTTP 4xx/5xx indicate dead or restricted endpoints.")
print("Use this to diagnose harness failures.")
PYEOF
