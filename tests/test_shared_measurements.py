"""Small independent checks for shared WGS84 measurements."""

import json
import math
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flypath_engine.measurements import route_distance_m, survey_area_m2  # noqa: E402


def test_wgs84_measurements():
    equator = [
        {"latitude_deg": 0, "longitude_deg": 0},
        {"latitude_deg": 0, "longitude_deg": 1},
    ]
    assert math.isclose(route_distance_m(equator), 111_319.490793, abs_tol=.001)

    request = json.loads(
        Path("tests/shared_engine/fixtures/reported-mission-126-input.json").read_text()
    )
    area_ha = survey_area_m2(request["survey_area"]) / 10_000
    assert 11.1 < area_ha < 11.3, area_ha

    first = request["survey_area"]
    shifted = {
        "exterior": [
            {**point, "longitude_deg": point["longitude_deg"] + .01}
            for point in first["exterior"]
        ],
        "holes": [],
    }
    multipart = survey_area_m2({"parts": [first, shifted]})
    assert math.isclose(multipart, survey_area_m2(first) + survey_area_m2(shifted))


if __name__ == "__main__":
    test_wgs84_measurements()
    print("PASS  test_wgs84_measurements")
