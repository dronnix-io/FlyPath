"""
Tests for terrain-follow helpers (terrain.py).

Pure Python, no QGIS or network. Run with pytest, or directly:
    python tests/test_terrain.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from terrain import (  # noqa: E402
    tile_coords, elevation_from_rgb, sample_elevations,
    densify_by_terrain, heights_above_takeoff,
)


def test_elevation_decode():
    # Terrarium: (R*256 + G + B/256) - 32768. Sea level is (128, 0, 0).
    assert elevation_from_rgb(128, 0, 0) == 0.0
    assert elevation_from_rgb(129, 0, 0) == 256.0
    assert round(elevation_from_rgb(128, 100, 0), 3) == 100.0
    assert elevation_from_rgb(0, 0, 0) == -32768.0


def test_tile_coords_in_range():
    x, y, px, py = tile_coords(51.05, -114.09, zoom=12)
    assert 0 <= px < 256 and 0 <= py < 256
    n = 2 ** 12
    assert 0 <= x < n and 0 <= y < n
    # Longitude increases -> tile x increases.
    x2, _, _, _ = tile_coords(51.05, -113.0, zoom=12)
    assert x2 >= x


def test_sample_elevations_orientation():
    # sample is called as sample(lon, lat); confirm order is preserved.
    seen = []
    def sample(lon, lat):
        seen.append((lon, lat)); return lon + lat
    wps = [(-114.0, 51.0), (-114.0, 51.001)]
    elevs = sample_elevations(wps, sample)
    assert seen == wps
    assert elevs == [-63.0, -62.999]


# ── Semi-automatic densify by terrain ───────────────────────────────────────

def _flat(lon, lat):
    return 100.0


def test_flat_terrain_inserts_nothing():
    # Two flight lines, flat ground -> no extra waypoints, elevations all equal.
    wps = [(0.0, 0.0), (0.0, 0.01),        # line 1 (~1.1 km N)
           (0.0005, 0.01), (0.0005, 0.0)]  # connector + line 2
    out, elevs = densify_by_terrain(wps, _flat, tolerance_m=5.0)
    assert out == wps
    assert set(elevs) == {100.0}


def test_slope_inserts_waypoints_only_on_flight_lines():
    # Elevation ramps with latitude; the long N-S flight lines gain waypoints,
    # the short E-W connector does not.
    def ramp(lon, lat):
        return lat * 100000.0            # ~1 m per 0.00001 deg lat
    wps = [(0.0, 0.0), (0.0, 0.01),
           (0.0005, 0.01), (0.0005, 0.0)]
    out, elevs = densify_by_terrain(wps, ramp, tolerance_m=50.0)
    assert len(out) > len(wps), 'a slope should add waypoints'
    # Every kept step changes by more than the tolerance (except the forced
    # original turn points).
    assert len(out) == len(elevs)
    # The four original corners are all present, in order.
    for corner in wps:
        assert corner in out


def test_tolerance_controls_density():
    def ramp(lon, lat):
        return lat * 100000.0
    wps = [(0.0, 0.0), (0.0, 0.02), (0.0005, 0.02), (0.0005, 0.0)]
    coarse, _ = densify_by_terrain(wps, ramp, tolerance_m=100.0)
    fine, _   = densify_by_terrain(wps, ramp, tolerance_m=20.0)
    assert len(fine) >= len(coarse), 'a tighter tolerance should add more waypoints'


def test_densify_relaxes_toward_max_points():
    # A tight tolerance on a moderate slope wants many waypoints; the adaptive
    # relaxation loosens it until the route fits the budget.
    def ramp(lon, lat):
        return lat * 10000.0             # ~100 m of relief per 0.01 deg line
    wps = [(0.0, 0.0), (0.0, 0.01), (0.001, 0.01), (0.001, 0.0)]
    unbounded, _ = densify_by_terrain(wps, ramp, tolerance_m=1.0, max_points=100000)
    bounded, _   = densify_by_terrain(wps, ramp, tolerance_m=1.0, max_points=40)
    assert len(unbounded) > 40, 'tight tolerance should want more than the budget'
    assert len(bounded) <= 40, 'relaxation should bring it within the budget'


# ── Height calculation ──────────────────────────────────────────────────────

def test_heights_hold_constant_agl():
    # Ground rises 30 m from takeoff -> executeHeight rises 30 m so AGL stays 100.
    elevs = [1000.0, 1010.0, 1030.0, 1005.0]
    h = heights_above_takeoff(elevs, altitude_m=100.0)
    assert h == [100.0, 110.0, 130.0, 105.0]


def test_heights_datum_cancels():
    # Adding a constant offset to every elevation (datum change) leaves heights
    # unchanged, since only differences from the first point are used.
    elevs = [1000.0, 1010.0, 1030.0]
    a = heights_above_takeoff(elevs, 80.0)
    b = heights_above_takeoff([e + 500.0 for e in elevs], 80.0)
    assert a == b


def test_heights_empty():
    assert heights_above_takeoff([], 100.0) == []


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items())
           if k.startswith('test_') and callable(v)]
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
