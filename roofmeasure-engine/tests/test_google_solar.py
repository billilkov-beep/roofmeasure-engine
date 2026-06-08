"""Offline test: feed a realistic Google Solar API response into the parser/adapter.

Fixture is built from the documented response shape, NOT a real API call (we
can't reach the internet from the test sandbox). When the user runs the engine
on their server, they'll hit the real API.

Reference for the shape:
https://developers.google.com/maps/documentation/solar/reference/rest/v1/buildingInsights/findClosest
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from roofmeasure.providers.google_solar import (
    parse_building_insights,
    solar_result_to_measurement_dict,
)


# A trimmed but realistic buildingInsights response for a hip-roof bungalow.
FIXTURE = {
    "name": "buildings/ChIJ_xxxxx",
    "center": {"latitude": 43.6511, "longitude": -79.3849},
    "imageryDate": {"year": 2024, "month": 6, "day": 12},
    "imageryProcessedDate": {"year": 2024, "month": 7, "day": 1},
    "imageryQuality": "HIGH",
    "postalCode": "M5V 1A1",
    "administrativeArea": "ON",
    "regionCode": "CA",
    "solarPotential": {
        "wholeRoofStats": {
            "areaMeters2": 176.1,
            "sunshineQuantiles": [],
            "groundAreaMeters2": 150.0
        },
        "buildingStats": {
            "areaMeters2": 150.0
        },
        "roofSegmentStats": [
            {
                "pitchDegrees": 26.57,         # 6/12 pitch
                "azimuthDegrees": 89.9,
                "stats": {"areaMeters2": 63.3},
                "center": {"latitude": 43.6511, "longitude": -79.38483},
                "boundingBox": {
                    "sw": {"latitude": 43.65095, "longitude": -79.38485},
                    "ne": {"latitude": 43.65125, "longitude": -79.38477}
                },
                "planeHeightAtCenterMeters": 6.02
            },
            {
                "pitchDegrees": 26.57,
                "azimuthDegrees": 269.9,
                "stats": {"areaMeters2": 65.9},
                "center": {"latitude": 43.6511, "longitude": -79.38497},
                "planeHeightAtCenterMeters": 6.02
            },
            {
                "pitchDegrees": 26.57,
                "azimuthDegrees": 0.1,
                "stats": {"areaMeters2": 23.4},
                "center": {"latitude": 43.65126, "longitude": -79.3849},
                "planeHeightAtCenterMeters": 6.02
            },
            {
                "pitchDegrees": 26.57,
                "azimuthDegrees": 179.9,
                "stats": {"areaMeters2": 23.4},
                "center": {"latitude": 43.65094, "longitude": -79.3849},
                "planeHeightAtCenterMeters": 6.02
            }
        ]
    }
}


def main():
    print("Parsing Solar API fixture...")
    solar = parse_building_insights(FIXTURE)
    print(f"  total_area_m2     : {solar.total_area_m2}")
    print(f"  footprint_area_m2 : {solar.footprint_area_m2}")
    print(f"  imagery_date      : {solar.imagery_date}")
    print(f"  imagery_quality   : {solar.imagery_quality}")
    print(f"  facets            : {len(solar.facets)}")
    for f in solar.facets:
        print(
            f"    id={f['id']:<2} area={f['areaM2']:>5} m^2  "
            f"pitch={f['pitch']:>5} ({f['pitchDeg']:>5} deg)  "
            f"azimuth={f['azimuthDeg']:>6}"
        )

    # --- assertions ---
    assert len(solar.facets) == 4, f"expected 4 facets, got {len(solar.facets)}"
    assert solar.imagery_quality == "HIGH"
    assert solar.imagery_date == "2024-06-12"

    # Pitch: 26.57 degrees should map to 6/12 (tan(26.57) ~= 0.5)
    pitches = [f["pitch"] for f in solar.facets]
    assert all(p == "6/12" for p in pitches), f"expected all 6/12, got {pitches}"

    # Azimuth: should match the fixture
    azimuths = sorted(round(f["azimuthDeg"]) for f in solar.facets)
    assert azimuths == [0, 90, 180, 270], f"expected cardinal azimuths, got {azimuths}"
    print("[OK] facet parse: 4 facets, all 6/12, cardinal azimuths")

    print("\nMapping to RoofMeasurement-shaped dict...")
    m = solar_result_to_measurement_dict(solar, "123 Sample St, Toronto, ON")
    print(f"  roofAreaSqFt        : {m['roofAreaSqFt']}")
    print(f"  footprintSqFt       : {m['footprintSqFt']}")
    print(f"  roofingSquares      : {m['roofingSquares']}")
    print(f"  predominantPitch    : {m['predominantPitch']}")
    print(f"  facetCount          : {m['facetCount']}")
    print(f"  confidenceScore     : {m['confidenceScore']}")
    print(f"  estimatedCostLow    : ${int(m['estimatedCostLow']):,}")
    print(f"  estimatedCostHigh   : ${int(m['estimatedCostHigh']):,}")
    print(f"  dataSources.imageryDate    : {m['dataSources']['imageryDate']}")
    print(f"  dataSources.imageryQuality : {m['dataSources']['imageryQuality']}")

    # --- output-shape assertions: must match the existing PreliminaryMeasurement contract ---
    required_keys = {
        "roofAreaSqFt", "footprintSqFt", "roofingSquares", "suggestedWastePercent",
        "quoteReadySqFt", "quoteReadySquares", "estimatedCostLow", "estimatedCostHigh",
        "pitchSummary", "predominantPitch", "facetCount", "sourceSummary",
        "locationSummary", "disclaimer", "confidenceScore", "facets", "edges",
        "obstructions", "lineMeasurements", "pitchAreas", "facetAreas",
        "measurementNotes", "dataSources",
    }
    missing = required_keys - set(m.keys())
    assert not missing, f"missing required keys: {missing}"
    print("[OK] output shape matches RoofMeasurement contract")

    # 176.1 m^2 * 10.7639 = 1895.6 sq ft (within rounding of demo_offline output)
    assert 1880 < m["roofAreaSqFt"] < 1910, f"roof area off: {m['roofAreaSqFt']}"
    assert m["predominantPitch"] == "6/12"
    assert m["confidenceScore"] == 92  # HIGH quality -> 92
    print("[OK] derived metrics correct")

    # Check the line measurements degrade gracefully (Solar API doesn't provide them)
    lm = m["lineMeasurements"]
    assert lm["ridgesFt"] == 0.0
    assert lm["hipsFt"] == 0.0
    print("[OK] line measurements correctly zeroed (Solar API doesn't provide edges)")

    print("\nAll Solar API adapter assertions passed.")


if __name__ == "__main__":
    main()
