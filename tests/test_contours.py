"""
Tests for contours.py (marching-squares contour extraction).

Pure Python, no QGIS. Run with pytest, or directly:
    python tests/test_contours.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contours import nice_levels, contour_segments        # noqa: E402


def test_nice_levels_are_round_multiples():
    assert nice_levels(0.0, 10.0, 2.0) == [0, 2, 4, 6, 8, 10]
    # only the multiples inside the span
    assert nice_levels(1.0, 9.5, 2.0) == [2, 4, 6, 8]
    # negatives handled
    assert nice_levels(-5.0, 5.0, 5.0) == [-5, 0, 5]


def test_nice_levels_empty_when_span_too_narrow():
    assert nice_levels(3.1, 3.9, 2.0) == []          # no multiple of 2 inside


def test_nice_levels_rejects_bad_interval():
    try:
        nice_levels(0, 10, 0)
        assert False, 'expected ValueError'
    except ValueError:
        pass


def test_ramp_in_x_gives_vertical_contours():
    # value == x, so the level-L contour is the vertical line x == L
    xs = [0.0, 1.0, 2.0, 3.0, 4.0]
    ys = [0.0, 1.0, 2.0]
    values = [[x for x in xs] for _ in ys]
    segs = contour_segments(xs, ys, values, [2.0])
    assert segs, 'expected a contour at x == 2'
    for level, (p1, p2) in segs:
        assert level == 2.0
        assert math.isclose(p1[0], 2.0) and math.isclose(p2[0], 2.0)


def test_flat_grid_has_no_contours():
    xs = [0.0, 1.0, 2.0]
    ys = [0.0, 1.0, 2.0]
    values = [[5.0] * 3 for _ in range(3)]
    assert contour_segments(xs, ys, values, [1.0, 5.0, 9.0]) == []


def test_none_samples_drop_their_cells():
    # a 2x2 grid (one cell); a missing corner drops it entirely
    xs = [0.0, 1.0]
    ys = [0.0, 1.0]
    values = [[0.0, 4.0], [4.0, None]]
    assert contour_segments(xs, ys, values, [2.0]) == []
    # with the corner present, the cell yields a segment at level 2
    values = [[0.0, 4.0], [4.0, 8.0]]
    assert contour_segments(xs, ys, values, [2.0])


def test_level_outside_range_yields_nothing():
    xs = [0.0, 1.0]
    ys = [0.0, 1.0]
    values = [[0.0, 1.0], [1.0, 2.0]]
    assert contour_segments(xs, ys, values, [9.0]) == []


def test_single_cone_contour_closes_around_peak():
    # a symmetric hill: the mid-level contour should ring the centre, so every
    # crossing point sits at a constant radius-ish band (all segments produced)
    xs = [-2.0, -1.0, 0.0, 1.0, 2.0]
    ys = [-2.0, -1.0, 0.0, 1.0, 2.0]
    values = [[10.0 - (x * x + y * y) for x in xs] for y in ys]
    segs = contour_segments(xs, ys, values, [6.0])   # ring at x^2+y^2 == 4
    assert len(segs) >= 4, 'a closed ring should cross several cells'
    for _lvl, (p1, p2) in segs:
        for px, py in (p1, p2):
            r = math.hypot(px, py)
            assert 1.5 < r < 2.5, r


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
