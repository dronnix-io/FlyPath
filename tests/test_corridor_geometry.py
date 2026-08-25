"""
Tests for the pure corridor-geometry helpers (corridor_geometry.py).

Pure Python, no QGIS. Run with pytest, or directly:
    python tests/test_corridor_geometry.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from corridor_geometry import (  # noqa: E402
    polyline_length, sample_polyline, compute_pass_offsets, snake_passes,
)


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


# ── sample_polyline ────────────────────────────────────────────────────────

def test_sample_straight_uniform():
    pts = sample_polyline([(0, 0), (10, 0)], 2)
    assert pts[0] == (0, 0)
    assert pts[-1] == (10, 0)
    # 0,2,4,6,8,10
    assert len(pts) == 6
    gaps = [_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    assert all(abs(g - 2) < 1e-6 for g in gaps)


def test_sample_includes_endpoint_when_not_multiple():
    pts = sample_polyline([(0, 0), (10, 0)], 3)
    # 0,3,6,9 then the final 10 is appended so the end is covered
    assert pts[0] == (0, 0)
    assert pts[-1] == (10, 0)
    assert len(pts) == 5


def test_sample_keeps_all_vertices():
    # Full-auto must fly through every vertex (no cut corners), with photo
    # points added between them.
    coords = [(0, 0), (100, 0), (100, 80), (220, 80)]   # two bends
    pts = sample_polyline(coords, 30)
    for v in coords:
        assert any(abs(p[0] - v[0]) < 1e-6 and abs(p[1] - v[1]) < 1e-6
                   for p in pts), f'vertex {v} missing from full-auto sampling'
    # and no gap longer than the spacing
    gaps = [_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    assert all(g <= 30 + 1e-6 for g in gaps)


def test_sample_follows_corner_by_arc_length():
    # An L-shape: spacing is measured along the path, not straight-line.
    pts = sample_polyline([(0, 0), (5, 0), (5, 5)], 3)
    assert pts[0] == (0, 0)
    assert pts[-1] == (5, 5)
    # A point should sit near the corner region, past 3 m of arc length
    assert any(abs(p[0] - 5) < 1e-6 and 0 < p[1] < 5 for p in pts)


def test_sample_single_point():
    assert sample_polyline([(1, 1)], 2) == [(1, 1)]


def test_sample_empty():
    assert sample_polyline([], 2) == []


def test_sample_ignores_duplicate_vertices():
    pts = sample_polyline([(0, 0), (0, 0), (10, 0)], 5)
    assert pts[0] == (0, 0) and pts[-1] == (10, 0)


def test_polyline_length():
    assert abs(polyline_length([(0, 0), (3, 0), (3, 4)]) - 7.0) < 1e-9


# ── compute_pass_offsets ───────────────────────────────────────────────────

def test_single_pass_when_footprint_covers_corridor():
    # total width 6 <= footprint 8 -> one centre pass
    assert compute_pass_offsets(3.0, 6.0, 8.0) == [0.0]


def test_multiple_passes_symmetric_and_centered():
    # total 20, footprint 8 -> centres span (20-8)=12, centred on 0
    offs = compute_pass_offsets(10.0, 6.0, 8.0)
    assert offs == sorted(offs)
    assert abs(offs[0] + 6.0) < 1e-9 and abs(offs[-1] - 6.0) < 1e-9
    # symmetric about 0
    assert all(abs(a + b) < 1e-9 for a, b in zip(offs, reversed(offs)))


def test_pass_spacing_never_exceeds_line_spacing():
    offs = compute_pass_offsets(10.0, 6.0, 8.0)
    gaps = [offs[i + 1] - offs[i] for i in range(len(offs) - 1)]
    assert all(g <= 6.0 + 1e-9 for g in gaps)


def test_wide_corridor_more_passes_than_narrow():
    narrow = compute_pass_offsets(6.0, 6.0, 4.0)
    wide = compute_pass_offsets(30.0, 6.0, 4.0)
    assert len(wide) > len(narrow)


def test_zero_width_single_pass():
    assert compute_pass_offsets(0.0, 6.0, 4.0) == [0.0]


def test_passes_grow_one_at_a_time():
    # As the corridor widens, the pass count must climb 1, 2, 3, ... never jump.
    fp, ls = 72.0, 21.6          # ~Mini footprint at 50 m, 70% side overlap
    counts = [len(compute_pass_offsets(hw, ls, fp))
              for hw in [i * 1.0 for i in range(1, 120)]]
    assert counts[0] == 1        # narrow corridor -> single centre pass
    assert all(0 <= counts[i + 1] - counts[i] <= 1
               for i in range(len(counts) - 1)), 'pass count jumped by >1'
    assert max(counts) > 5       # eventually many passes for a wide corridor


def test_single_to_two_pass_transition_is_gradual():
    fp, ls = 72.0, 21.6
    assert len(compute_pass_offsets(36.0, ls, fp)) == 1   # total 72 == footprint
    assert len(compute_pass_offsets(40.0, ls, fp)) == 2   # total 80, just over


def test_passes_spaced_at_line_spacing_respecting_overlap():
    # Multiple passes are spaced exactly at the line spacing, so adjacent passes
    # meet the requested side overlap (not a tighter, over-overlapped spacing).
    hw, ls, fp = 60.0, 21.6, 72.0
    offs = compute_pass_offsets(hw, ls, fp)
    assert len(offs) >= 2
    gaps = [offs[i + 1] - offs[i] for i in range(len(offs) - 1)]
    assert all(abs(g - ls) < 1e-6 for g in gaps)      # exactly the line spacing
    # outermost footprint still reaches (covers) the corridor edge
    assert offs[-1] + fp / 2 >= hw - 1e-6


# ── snake_passes ───────────────────────────────────────────────────────────

def test_snake_reverses_alternate_passes():
    a = [(0, 0), (0, 1)]
    b = [(1, 0), (1, 1)]
    c = [(2, 0), (2, 1)]
    route = snake_passes([a, b, c])
    assert route == [(0, 0), (0, 1),         # a forward
                     (1, 1), (1, 0),          # b reversed
                     (2, 0), (2, 1)]          # c forward


def test_snake_empty():
    assert snake_passes([]) == []


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
