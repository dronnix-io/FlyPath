"""
corridor_geometry.py
--------------------
Pure-Python geometry helpers for corridor (linear) mapping. No QGIS imports,
so they are fast and unit-testable on their own (see tests/test_corridor_geometry.py).

The QGIS-dependent orchestration (CRS transforms, parallel offsetting of the
centre line) lives in corridor_planner.py, which calls into these helpers. This
mirrors the existing split between grid_route.py (pure) and grid_planner.py (QGIS).

Everything here works in a flat metric coordinate system (metres), i.e. after the
centre line has already been reprojected to UTM.
"""

import math


def _dist(a, b):
    """Planar distance between two (x, y) points."""
    return math.hypot(b[0] - a[0], b[1] - a[1])


def polyline_length(coords):
    """Total length of a polyline given as a list of (x, y) points."""
    return sum(_dist(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def sample_polyline(coords, spacing):
    """Resample a polyline at ~`spacing` (metres) while keeping EVERY original
    vertex.

    Each segment between two consecutive vertices is split into equal steps no
    longer than `spacing`, so the drone passes exactly through every vertex (no
    cut corners) and photo waypoints fall in between. Used for full-automatic
    corridor passes, where each waypoint is a photo location and the flight path
    must still follow the line's bends.

    `coords` is a list of (x, y) in metres. Degenerate input (empty or a single
    point) is returned as-is.
    """
    if not coords:
        return []
    spacing = max(float(spacing), 0.5)

    # Drop consecutive duplicate vertices so zero-length segments don't stall.
    clean = [coords[0]]
    for c in coords[1:]:
        if _dist(clean[-1], c) > 1e-9:
            clean.append(c)
    if len(clean) == 1:
        return [clean[0]]

    out = [clean[0]]
    for a, b in zip(clean, clean[1:]):
        seg_len = _dist(a, b)
        n = max(1, int(math.ceil(seg_len / spacing)))   # steps for this segment
        for k in range(1, n + 1):                        # k == n emits vertex b
            t = k / n
            out.append((a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t))
    return out


def compute_pass_offsets(half_width_m, line_spacing_m, footprint_across_m):
    """Signed cross-track offsets (metres) for the parallel passes that cover a
    corridor extending `half_width_m` on each side of the centre line.

    The result is centred and symmetric about 0 (the centre line). A positive
    and a negative offset are opposite sides of the line. Adjacent passes are
    spaced at most `line_spacing_m` apart, so the requested side overlap is
    always met or exceeded.

    A single centre pass is returned when the camera footprint already spans the
    whole corridor, matching the field expectation that a narrow corridor needs
    just one line down the middle.
    """
    half_width_m = max(float(half_width_m), 0.0)
    line_spacing_m = max(float(line_spacing_m), 0.5)
    footprint_across_m = max(float(footprint_across_m), 0.0)
    total = 2.0 * half_width_m

    # A single centre pass already spans the whole corridor.
    if total <= footprint_across_m + 1e-9 or total <= 1e-9:
        return [0.0]

    # Extra passes are only needed for the width the centre footprint can't
    # reach, so the pass count grows one at a time as the corridor widens (never
    # in a sudden jump). Passes are then spaced at the full line spacing and
    # centred on the line, so adjacent passes meet the requested side overlap
    # exactly (not more). The outer passes' footprints extend a little past the
    # corridor edge, which keeps the overlap even right out to the edge.
    n_extra = math.ceil((total - footprint_across_m) / line_spacing_m)
    n_passes = n_extra + 1
    centre = (n_passes - 1) / 2.0
    return [(i - centre) * line_spacing_m for i in range(n_passes)]


def snake_passes(passes):
    """Flatten a list of passes (each a list of points) into one route,
    reversing every other pass so consecutive passes join end-to-start
    (boustrophedon), keeping transit between passes short.
    """
    route = []
    for i, pts in enumerate(passes):
        route.extend(list(reversed(pts)) if i % 2 else list(pts))
    return route
