"""
Tests for takeoff_zone.py (the takeoff-zone core logic).

Pure Python, no QGIS. Run with pytest, or directly:
    python tests/test_takeoff_zone.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from takeoff_zone import (gsd_variance_pct, within_signal_range, takeoff_zone,
                          sample_grid, search_bounds)          # noqa: E402


def test_gsd_variance_scales_with_tolerance_over_altitude():
    assert math.isclose(gsd_variance_pct(2.0, 90.0), 100.0 * 2 / 90)
    assert math.isclose(gsd_variance_pct(1.0, 100.0), 1.0)
    # tighter tolerance or higher altitude means less variance
    assert gsd_variance_pct(1.0, 100.0) < gsd_variance_pct(2.0, 100.0)
    assert gsd_variance_pct(2.0, 200.0) < gsd_variance_pct(2.0, 100.0)


def test_gsd_variance_zero_altitude_is_safe():
    assert gsd_variance_pct(2.0, 0.0) == 0.0


def test_within_signal_range_uses_the_farthest_point():
    mission = [(0.0, 0.0), (300.0, 0.0)]      # farthest point is 300 m away
    # a takeoff between them is within 200 m of both
    assert within_signal_range((150.0, 0.0), mission, 200.0)
    # right at one end it is 300 m from the other, so 200 m fails, 300 m passes
    assert not within_signal_range((0.0, 0.0), mission, 200.0)
    assert within_signal_range((0.0, 0.0), mission, 300.0)


def test_within_signal_range_empty_mission_has_no_limit():
    assert within_signal_range((9999.0, 9999.0), [], 10.0)


def test_zone_filters_by_elevation_band():
    # reference 100 m, tolerance 2 m: keep 98..102, drop the rest
    cands = [(0, 0, 100.0), (10, 0, 101.5), (20, 0, 97.0), (30, 0, 103.0)]
    zone = takeoff_zone(cands, [], ref_elevation=100.0, tolerance_m=2.0,
                        proximity_m=1e9)
    got = sorted(c[2] for c in zone)
    assert got == [100.0, 101.5]


def test_zone_filters_by_proximity():
    # all at the reference elevation, but only the near ones stay in range
    ref = 50.0
    mission = [(0.0, 0.0)]
    cands = [(50.0, 0.0, ref), (150.0, 0.0, ref), (250.0, 0.0, ref)]
    zone = takeoff_zone(cands, mission, ref_elevation=ref, tolerance_m=1.0,
                        proximity_m=200.0)
    xs = sorted(c[0] for c in zone)
    assert xs == [50.0, 150.0]        # 250 m one is out of range


def test_zone_skips_missing_elevation():
    cands = [(0, 0, None), (10, 0, 100.0)]
    zone = takeoff_zone(cands, [], ref_elevation=100.0, tolerance_m=1.0,
                        proximity_m=1e9)
    assert len(zone) == 1 and zone[0][2] == 100.0


def test_sample_grid_covers_bounds_inclusive():
    pts = sample_grid((0.0, 0.0, 100.0, 50.0), 50.0)
    xs = sorted(set(p[0] for p in pts))
    ys = sorted(set(p[1] for p in pts))
    assert xs == [0.0, 50.0, 100.0]
    assert ys == [0.0, 50.0]
    assert (0.0, 0.0) in pts and (100.0, 50.0) in pts


def test_sample_grid_zero_area_yields_corner():
    pts = sample_grid((5.0, 5.0, 5.0, 5.0), 10.0)
    assert pts == [(5.0, 5.0)]


def test_sample_grid_rejects_bad_spacing():
    try:
        sample_grid((0, 0, 10, 10), 0)
        assert False, 'expected ValueError'
    except ValueError:
        pass


def test_search_bounds_expands_by_margin():
    b = search_bounds([(10.0, 20.0), (30.0, 40.0)], 5.0)
    assert b == (5.0, 15.0, 35.0, 45.0)
    assert search_bounds([], 5.0) is None


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f'PASS  {fn.__name__}')
        except AssertionError as exc:
            failed += 1
            print(f'FAIL  {fn.__name__}: {exc}')
    print(f'\n{len(fns) - failed}/{len(fns)} passed')
    sys.exit(1 if failed else 0)
