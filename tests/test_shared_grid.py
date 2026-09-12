"""Compatibility check for the shared grid against the QGIS reference."""

import json
import math
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flypath_engine.grid import find_optimal_direction, generate_grid  # noqa: E402


def test_manual_directions_match_qgis_fixture():
    fixture = json.loads(Path("tests/shared_engine/fixtures/plugin-direction.json").read_text())
    profile = json.loads(Path("tests/shared_engine/profiles/mini3pro-v1.json").read_text())
    survey_area = {
        "exterior": [
            {"latitude_deg": latitude, "longitude_deg": longitude}
            for latitude, longitude in fixture["polygon"]
        ],
        "holes": [],
    }
    for case in fixture["cases"]:
        result = generate_grid(
            survey_area,
            altitude_m=80,
            shot_spacing_m=16,
            side_overlap=.7,
            direction_deg=case["direction"],
            margin_m=0,
            camera=profile["camera"],
        )
        actual = result["waypoints"]
        assert len(actual) == len(case["waypoints"]), case["direction"]
        for point, (expected_latitude, expected_longitude) in zip(actual, case["waypoints"]):
            error_m = math.hypot(
                (point["latitude_deg"] - expected_latitude) * 111_320,
                (point["longitude_deg"] - expected_longitude)
                * 111_320 * math.cos(math.radians(expected_latitude)),
            )
            assert error_m <= .05, (case["direction"], error_m)

    line_spacing = 80 * profile["camera"]["sensor_width_mm"] \
        / profile["camera"]["focal_length_mm"] * .3
    assert find_optimal_direction(survey_area, line_spacing) == fixture["autoDirection"]


if __name__ == "__main__":
    test_manual_directions_match_qgis_fixture()
    print("PASS  test_manual_directions_match_qgis_fixture")
