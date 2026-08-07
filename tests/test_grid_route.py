"""
Tests for the concave-safe flight-route ordering (grid_route.py).

Pure Python, no QGIS. Run with pytest, or directly:
    python tests/test_grid_route.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grid_route import (  # noqa: E402
    boustrophedon_route, decompose_cells,
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
