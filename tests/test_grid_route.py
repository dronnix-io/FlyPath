"""
Tests for the concave-safe flight-route ordering (grid_route.py).

Pure Python, no QGIS. Run with pytest, or directly:
    python tests/test_grid_route.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grid_route import (  # noqa: E402
    boustrophedon_route, decompose_cells, split_by_waypoint_count,
)


def _seglookup(columns):
    lut = {}
    for x, segs in columns:
        lut.setdefault(round(x, 9), set()).update(
            (round(a, 9), round(b, 9)) for a, b in segs)
    return lut


def _assert_no_pass_crosses_a_gap(columns, route):
    """Every vertical (same-x) leg in the route must be an actual in-polygon
    segment of that scan line. A pass flown across a gap would be a same-x leg
    that matches no segment."""
    lut = _seglookup(columns)
    for (x0, y0), (x1, y1) in zip(route, route[1:]):
        if abs(x0 - x1) < 1e-9:
            lo, hi = sorted((y0, y1))
            assert (round(lo, 9), round(hi, 9)) in lut.get(round(x0, 9), set()), (
                f'vertical leg at x={x0} spanning y {lo}->{hi} is not a real '
                f'segment; the drone would cross a gap')


def _covers_every_segment(columns, route):
    """Every scan-line segment is flown as a pass (a same-x leg)."""
    flown = set()
    for (x0, y0), (x1, y1) in zip(route, route[1:]):
        if abs(x0 - x1) < 1e-9:
            lo, hi = sorted((y0, y1))
            flown.add((round(x0, 9), round(lo, 9), round(hi, 9)))
    for x, segs in columns:
        for a, b in segs:
            assert (round(x, 9), round(a, 9), round(b, 9)) in flown, (
                f'segment at x={x} y {a}->{b} was never flown')


# ── Convex (single segment per line) — the classic lawnmower still works ────

def test_convex_rectangle():
    cols = [(float(x), [(0.0, 10.0)]) for x in range(6)]
    route = boustrophedon_route(cols)
    cells, _ = decompose_cells(cols)
    assert len(cells) == 1                            # one strip
    assert len(route) == 12                          # 2 turns per line
    _assert_no_pass_crosses_a_gap(cols, route)
    _covers_every_segment(cols, route)


# ── Concave "C": left lines split by a gap, right lines are whole ───────────

def _c_shape():
    cols = []
    for x in range(5):                               # left: two pieces (gap 10..20)
        cols.append((float(x), [(0.0, 10.0), (20.0, 30.0)]))
    for x in range(5, 10):                            # right: one piece, full height
        cols.append((float(x), [(0.0, 30.0)]))
    return cols


def test_concave_decomposes_into_three_strips():
    cells, adjacency = decompose_cells(_c_shape())
    assert len(cells) == 3                            # top, bottom, right
    # The two arms both connect to the right spine, but not to each other.
    degrees = sorted(len(a) for a in adjacency)
    assert degrees == [1, 1, 2]


def test_concave_never_flies_across_the_gap():
    cols = _c_shape()
    route = boustrophedon_route(cols)
    _assert_no_pass_crosses_a_gap(cols, route)        # the actual bug fix
    _covers_every_segment(cols, route)


def test_split_then_merge_shape():
    # One line, then a gap opens (split), then it closes again (merge).
    cols = [(0.0, [(0.0, 30.0)])]
    cols += [(float(x), [(0.0, 10.0), (20.0, 30.0)]) for x in range(1, 4)]
    cols += [(4.0, [(0.0, 30.0)])]
    route = boustrophedon_route(cols)
    _assert_no_pass_crosses_a_gap(cols, route)
    _covers_every_segment(cols, route)


def test_empty_input():
    assert boustrophedon_route([]) == []
    assert boustrophedon_route([(0.0, [])]) == []


# ── Densified passes (full-automatic capture: a waypoint per photo) ──────────

def _assert_passes_within_segments(columns, route, tol=1e-6):
    """Every vertical (same-x) leg must lie within a single segment of that
    column. A densified pass is many short legs, each inside the real segment;
    a gap-crossing leg would span two segments and fail this."""
    lut = {}
    for x, segs in columns:
        lut.setdefault(round(x, 9), []).extend(segs)
    for (x0, y0), (x1, y1) in zip(route, route[1:]):
        if abs(x0 - x1) < 1e-9:
            lo, hi = sorted((y0, y1))
            segs = lut.get(round(x0, 9), [])
            assert any(a - tol <= lo and hi <= b + tol for a, b in segs), (
                f'vertical leg at x={x0} y {lo}->{hi} is not within a segment')


def test_densify_convex_spacing_and_count():
    cols = [(float(x), [(0.0, 10.0)]) for x in range(4)]
    route = boustrophedon_route(cols, densify_spacing=2.5)
    # 10 m pass at 2.5 m -> 4 steps -> 5 points per pass; 4 columns -> 20 points.
    assert len(route) == 20
    _assert_passes_within_segments(cols, route)
    # No same-x leg longer than the spacing (+ float tolerance).
    for (x0, y0), (x1, y1) in zip(route, route[1:]):
        if abs(x0 - x1) < 1e-9:
            assert abs(y1 - y0) <= 2.5 + 1e-6


def test_densify_concave_stays_inside():
    route = boustrophedon_route(_c_shape(), densify_spacing=3.0)
    _assert_passes_within_segments(_c_shape(), route)


def test_densify_none_is_endpoints_only():
    cols = [(float(x), [(0.0, 10.0)]) for x in range(4)]
    assert len(boustrophedon_route(cols)) == 8          # 2 points per pass


# ── Waypoint-count split (full-automatic multi-battery / cap) ────────────────

def _wps(n):
    return [(float(i), 0.0) for i in range(n)]


def _assert_seam_shared_and_complete(pts, missions):
    # Consecutive missions share a seam point; concatenating without the shared
    # duplicates reproduces the original list, in order, with nothing lost.
    rebuilt = list(missions[0])
    for m in missions[1:]:
        assert m[0] == rebuilt[-1], 'missions must share a seam waypoint'
        rebuilt.extend(m[1:])
    assert rebuilt == list(pts)


def test_split_count_honours_battery_minimum():
    pts = _wps(100)
    missions = split_by_waypoint_count(pts, n_missions=3, max_waypoints=70)
    assert len(missions) == 3                          # cap (ceil(99/69)=2) < battery 3
    _assert_seam_shared_and_complete(pts, missions)


def test_split_count_raised_by_waypoint_cap():
    pts = _wps(300)
    missions = split_by_waypoint_count(pts, n_missions=2, max_waypoints=70)
    # ceil(299/69) = 5 missions needed to stay under the cap, above battery 2.
    assert len(missions) == 5
    assert all(len(m) <= 70 for m in missions)
    _assert_seam_shared_and_complete(pts, missions)


def test_split_never_exceeds_cap_various_sizes():
    for w in (2, 71, 139, 140, 141, 500, 999):
        pts = _wps(w)
        missions = split_by_waypoint_count(pts, n_missions=1, max_waypoints=70)
        assert all(len(m) <= 70 for m in missions), f'w={w} exceeded cap'
        _assert_seam_shared_and_complete(pts, missions)


def test_split_single_mission_when_small():
    assert split_by_waypoint_count(_wps(1), 5, 70) == [_wps(1)]
    assert len(split_by_waypoint_count(_wps(40), 1, 70)) == 1


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
