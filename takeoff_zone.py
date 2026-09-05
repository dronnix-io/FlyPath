"""
takeoff_zone.py
---------------
Pure-Python core for the takeoff-zone feature. No QGIS dependency, so it can be
unit-tested outside a QGIS runtime.

Consumer DJI missions fly relative to the takeoff point, so on non-flat terrain
the whole mission's height above ground (and therefore the GSD) shifts if the
drone launches from ground at a different elevation. For repeat/change-detection
mapping the pilot needs to take off from ground at (nearly) the same elevation
each time. Given DEM elevation samples over a search area, this selects the
candidate takeoff points that:

  1. sit within a tolerance of a reference elevation (the DEM elevation at the
     mission's first point), so the mission flies at the same height each time,
     and
  2. keep every mission point within the drone's signal range of the controller
     at the takeoff (a proximity cap, e.g. half the drone's transmission range).

All coordinates are in a metric (projected) CRS so distances are in metres; the
caller projects the DEM samples and mission points before calling in.
"""

import math


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def gsd_variance_pct(tolerance_m, altitude_m):
    """Percent GSD change implied by an elevation tolerance at this altitude.

    GSD is proportional to height above ground. A takeoff elevation off by
    `tolerance_m` shifts every waypoint's height above ground by that amount, so
    the GSD shifts by tolerance_m / altitude_m. Returns the +/- percentage
    (e.g. 2 m tolerance at 90 m altitude gives about 2.22)."""
    if altitude_m <= 0:
        return 0.0
    return 100.0 * float(tolerance_m) / float(altitude_m)


def within_signal_range(point, mission_points, proximity_m):
    """True if `point` is within `proximity_m` of every mission point.

    The controller sits at the takeoff point, so the drone's greatest distance
    from it is to the farthest mission point; keeping that within the proximity
    cap keeps the whole flight in signal range. Empty mission_points imposes no
    proximity limit."""
    return all(_dist(point, mp) <= proximity_m for mp in mission_points)


def takeoff_zone(candidates, mission_points, ref_elevation, tolerance_m,
                 proximity_m):
    """Select the takeoff-zone candidates.

    candidates:    [(x, y, elevation), ...] DEM samples over the search area.
    mission_points:[(x, y), ...] points the drone must stay in range of.
    ref_elevation: reference takeoff elevation (metres).
    tolerance_m:   elevation band half-width; a candidate qualifies when its
                   elevation is within +/- tolerance_m of ref_elevation.
    proximity_m:   max distance from a candidate to every mission point.

    Returns the qualifying [(x, y, elevation), ...] in input order."""
    zone = []
    for c in candidates:
        x, y, elev = c[0], c[1], c[2]
        if elev is None:
            continue
        if abs(elev - ref_elevation) > tolerance_m:
            continue
        if mission_points and not within_signal_range((x, y), mission_points,
                                                       proximity_m):
            continue
        zone.append((x, y, elev))
    return zone


def sample_grid(bounds, spacing_m):
    """Regular grid of (x, y) points covering bounds at `spacing_m`.

    bounds = (min_x, min_y, max_x, max_y). Edges are included, so a zero-area
    bound still yields the single corner point. The caller fills each point's
    elevation from the DEM."""
    min_x, min_y, max_x, max_y = bounds
    if spacing_m <= 0:
        raise ValueError('spacing_m must be positive')
    n_x = max(0, int(math.ceil((max_x - min_x) / spacing_m)))
    n_y = max(0, int(math.ceil((max_y - min_y) / spacing_m)))
    pts = []
    for i in range(n_x + 1):
        x = min_x + i * spacing_m
        for j in range(n_y + 1):
            pts.append((x, min_y + j * spacing_m))
    return pts


def search_bounds(mission_points, margin_m):
    """Bounding box (min_x, min_y, max_x, max_y) around the mission, expanded by
    `margin_m` on every side. This is the practical area to sample the DEM over;
    the proximity cap then prunes it. None if there are no mission points."""
    if not mission_points:
        return None
    xs = [p[0] for p in mission_points]
    ys = [p[1] for p in mission_points]
    return (min(xs) - margin_m, min(ys) - margin_m,
            max(xs) + margin_m, max(ys) + margin_m)
