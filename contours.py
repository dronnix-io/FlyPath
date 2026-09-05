"""
contours.py
-----------
Pure-Python marching-squares contour extraction. No QGIS dependency, so it can
be unit-tested outside a QGIS runtime.

The caller samples a DEM onto a regular grid (in a metric CRS) and passes the
grid in; this returns the contour line segments at the requested levels. A grid
cell that touches a missing sample (None) is skipped, so gaps in DEM coverage
simply leave gaps in the contours instead of drawing across them.
"""

import math


def nice_levels(min_v, max_v, interval):
    """Contour levels at whole multiples of `interval` covering [min_v, max_v].

    Using round multiples (…, -interval, 0, interval, …) rather than offsets from
    the data keeps the labelled elevations tidy. Returns [] when the span is
    narrower than one interval (nothing to draw)."""
    if interval <= 0:
        raise ValueError('interval must be positive')
    if max_v < min_v:
        min_v, max_v = max_v, min_v
    start = int(math.ceil(min_v / interval))
    end = int(math.floor(max_v / interval))
    return [k * interval for k in range(start, end + 1)]


def _interp(p1, v1, p2, v2, level):
    """Point on edge p1->p2 where the value crosses `level`."""
    if v2 == v1:
        t = 0.5
    else:
        t = (level - v1) / (v2 - v1)
    return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))


# Marching-squares case table. Corner bits: a=1 (bottom-left), b=2 (bottom-
# right), c=4 (top-right), d=8 (top-left); a bit is set when that corner is at or
# above the level. Each case lists the edge pairs to connect, where edges are
# B(a-b), R(b-c), T(c-d), L(d-a). The two saddle cases (5, 10) emit two segments.
_CASES = {
    0: [], 15: [],
    1: [('L', 'B')], 14: [('L', 'B')],
    2: [('B', 'R')], 13: [('B', 'R')],
    4: [('R', 'T')], 11: [('T', 'R')],
    8: [('T', 'L')], 7: [('L', 'T')],
    3: [('L', 'R')], 12: [('L', 'R')],
    6: [('B', 'T')], 9: [('B', 'T')],
    5: [('L', 'T'), ('B', 'R')],
    10: [('L', 'B'), ('T', 'R')],
}


def contour_segments(xs, ys, values, levels):
    """March a regular grid and return [(level, (p1, p2)), ...] line segments.

    xs:     column x-coordinates, length W (ascending).
    ys:     row y-coordinates, length H (ascending).
    values: values[row][col], row indexes ys and col indexes xs; a None sample
            drops every cell that touches it.
    levels: iterable of contour levels.
    """
    levels = list(levels)
    out = []
    H, W = len(ys), len(xs)
    for j in range(H - 1):
        row0, row1 = values[j], values[j + 1]
        for i in range(W - 1):
            a, b = row0[i], row0[i + 1]
            c, d = row1[i + 1], row1[i]
            if a is None or b is None or c is None or d is None:
                continue
            pa = (xs[i], ys[j])
            pb = (xs[i + 1], ys[j])
            pc = (xs[i + 1], ys[j + 1])
            pd = (xs[i], ys[j + 1])
            lo = a if a < b else b
            if c < lo:
                lo = c
            if d < lo:
                lo = d
            hi = a if a > b else b
            if c > hi:
                hi = c
            if d > hi:
                hi = d
            for level in levels:
                if level < lo or level > hi:
                    continue                 # cell does not straddle this level
                idx = ((1 if a >= level else 0) | (2 if b >= level else 0)
                       | (4 if c >= level else 0) | (8 if d >= level else 0))
                segs = _CASES[idx]
                if not segs:
                    continue
                edges = {
                    'B': lambda: _interp(pa, a, pb, b, level),
                    'R': lambda: _interp(pb, b, pc, c, level),
                    'T': lambda: _interp(pc, c, pd, d, level),
                    'L': lambda: _interp(pd, d, pa, a, level),
                }
                for e1, e2 in segs:
                    out.append((level, (edges[e1](), edges[e2]())))
    return out
