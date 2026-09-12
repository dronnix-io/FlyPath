"""The vendored engine keeps mission statistics identical across clients."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flypath_engine.statistics import mission_statistics  # noqa: E402


def test_semi_auto_statistics():
    stats = mission_statistics(
        route_distance_m=24_190.247243,
        speed_m_s=5,
        capture_mode="semi",
        photo_interval_s=2,
        battery_minutes=24,
    )
    assert math.isclose(stats["flight_seconds"], 4_838.0494486)
    assert stats["photo_count"] == 2_419
    assert stats["battery_count"] == 4


if __name__ == "__main__":
    test_semi_auto_statistics()
    print("PASS  test_semi_auto_statistics")
