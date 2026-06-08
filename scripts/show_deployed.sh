#!/bin/bash
# Engine state inspector — what's live on the VPS right now?
#
# Greps engine files for known v3.X patch markers and reports presence/absence.
# Run any time to confirm which patches survived restarts / deploys / rollbacks.

ENGINE=/home/roofmeasure/engine/roofmeasure
TESTS=/home/roofmeasure/engine/tests

echo "================================================================"
echo "  ENGINE STATE — $(date -u +%FT%TZ)"
echo "================================================================"
echo

# Helper: report a marker
check() {
    local file="$1" marker="$2" label="$3"
    if [ ! -f "$file" ]; then
        printf "  [%-30s] %s  (file missing)\n" "$label" "—"
        return
    fi
    if grep -q "$marker" "$file"; then
        printf "  \033[32m[%-30s] LIVE\033[0m\n" "$label"
    else
        printf "  \033[33m[%-30s] not present\033[0m\n" "$label"
    fi
}

echo "=== segmentation_v2.py ==="
check "$ENGINE/segmentation_v2.py" "footprint_area_m2" "v3.4 footprint-area override"
check "$ENGINE/segmentation_v2.py" "footprint_vertex_count" "v3.5 adaptive overhang param"
check "$ENGINE/segmentation_v2.py" "v <= 5: OVERHANG_FACTOR = 1.40" "v3.5 adaptive overhang table"
echo

echo "=== footprint_v2.py ==="
check "$ENGINE/footprint_v2.py" "_union_nearby_polygons" "v3.6 union nearby buildings"
check "$ENGINE/footprint_v2.py" "MS_URL_FALLBACKS_US" "v3.7 MS URL chain"
check "$ENGINE/footprint_v2.py" "SEARCH_RADII_M = \[80, 150, 300, 500, 1000\]" "v3.11 expanded OSMnx radii"
check "$ENGINE/footprint_v2.py" "ms_global_buildings" "v3.11 MS Global ML"
echo

echo "=== hybrid_pipeline.py ==="
check "$ENGINE/hybrid_pipeline.py" "footprint_vertex_count" "v3.5 wireup"
check "$ENGINE/hybrid_pipeline.py" "v3.8 direct solar" "v3.8 direct Solar API"
check "$ENGINE/hybrid_pipeline.py" "_merge_hybrid_facets" "v3.9 Solar facet merging"
check "$ENGINE/hybrid_pipeline.py" "v3.10 absorb tiny\|absorbed.*tiny" "v3.10 tiny-facet absorption"
check "$ENGINE/hybrid_pipeline.py" "v3.11 solar quality gate\|solar quality gate" "v3.11 Solar quality gate"
check "$ENGINE/hybrid_pipeline.py" "nrcan_hrdem_provider" "v3.11 NRCan wired"
check "$ENGINE/hybrid_pipeline.py" "v3.12 Solar quality fallback" "v3.12 Solar HIGH/MED/LOW fallback"
echo

echo "=== provider modules ==="
if [ -f "$ENGINE/nrcan_hrdem_provider.py" ]; then
    SIZE=$(stat -c%s "$ENGINE/nrcan_hrdem_provider.py")
    printf "  \033[32m[%-30s] LIVE\033[0m ($SIZE bytes)\n" "NRCan HRDEM provider"
else
    printf "  \033[33m[%-30s] not present\033[0m\n" "NRCan HRDEM provider"
fi
if [ -f "$ENGINE/ms_global_buildings.py" ]; then
    SIZE=$(stat -c%s "$ENGINE/ms_global_buildings.py")
    printf "  \033[32m[%-30s] LIVE\033[0m ($SIZE bytes)\n" "MS Global ML Buildings"
else
    printf "  \033[33m[%-30s] not present\033[0m\n" "MS Global ML Buildings"
fi
echo

echo "=== ground-truth harness ==="
if [ -f "$TESTS/ground_truth_harness.py" ]; then
    if grep -q "ThreadPoolExecutor" "$TESTS/ground_truth_harness.py"; then
        printf "  \033[32m[%-30s] LIVE (parallel)\033[0m\n" "Harness"
    else
        printf "  \033[33m[%-30s] serial only\033[0m\n" "Harness"
    fi
    if [ -f "$TESTS/ground_truth.csv" ]; then
        N=$(($(wc -l < "$TESTS/ground_truth.csv") - 1))
        printf "  \033[32m[%-30s] $N rows\033[0m\n" "ground_truth.csv"
    fi
fi
echo

echo "=== auto-test watcher ==="
if systemctl is-active --quiet roofmeasure-auto-test 2>/dev/null; then
    printf "  \033[32m[%-30s] ACTIVE\033[0m\n" "auto-test service"
    echo "    Latest activity:"
    tail -3 /tmp/auto_test_watch.log 2>/dev/null | sed 's/^/      /'
else
    printf "  \033[33m[%-30s] not running\033[0m\n" "auto-test service"
fi
echo

echo "=== environment ==="
if [ -n "$GOOGLE_SOLAR_API_KEY" ]; then
    printf "  \033[32m[%-30s] in shell (starts %s...)\033[0m\n" "GOOGLE_SOLAR_API_KEY" "${GOOGLE_SOLAR_API_KEY:0:6}"
else
    printf "  \033[33m[%-30s] not in current shell\033[0m\n" "GOOGLE_SOLAR_API_KEY"
fi
if [ -f /etc/systemd/system/roofmeasure-engine.service.d/google-api.conf ]; then
    printf "  \033[32m[%-30s] LIVE\033[0m\n" "systemd override"
else
    printf "  \033[33m[%-30s] not configured\033[0m\n" "systemd override"
fi
echo

echo "=== cache ==="
if [ -d /var/cache/roofmeasure/laz ]; then
    N=$(find /var/cache/roofmeasure/laz -name "*.laz" 2>/dev/null | wc -l)
    SIZE=$(du -sh /var/cache/roofmeasure/laz 2>/dev/null | cut -f1)
    printf "  LAZ files cached: %d  (%s total)\n" "$N" "$SIZE"
fi
if [ -d /var/cache/roofmeasure/nrcan_hrdem ]; then
    N=$(find /var/cache/roofmeasure/nrcan_hrdem -type f 2>/dev/null | wc -l)
    printf "  NRCan HRDEM cached: %d files\n" "$N"
fi
if [ -d /var/cache/roofmeasure/ms-footprints ]; then
    N=$(find /var/cache/roofmeasure/ms-footprints -type f 2>/dev/null | wc -l)
    printf "  MS Footprints cached: %d files\n" "$N"
fi
echo

echo "================================================================"
echo "  Done. Use the LIVE/not-present indicators to verify deploy state."
echo "================================================================"
