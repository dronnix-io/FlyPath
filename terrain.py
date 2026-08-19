"""
terrain.py
----------
Terrain-follow helpers: turn a flight route into per-waypoint heights that hold a
constant height above ground, using ground elevations sampled from a DEM.

Pure Python (no QGIS). The elevation source is injected as a `sample(lon, lat)`
callable, so the densify / height logic is unit-testable without any network or
QGIS. The runtime source is the free AWS "Terrarium" global DEM tile set (no key,
no registration); the QGIS side fetches those tiles into memory and decodes them
to provide `sample` (see flypath_dialog._TerrainSampler). Nothing is written to
disk.

    tile:      https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png
    elevation: (R * 256 + G + B / 256) - 32768   metres above sea level
"""

import math

# AWS Open Data Terrarium tiles. z12 is ~25-38 m/pixel at survey latitudes,
# matching the ~30 m source data; good enough for bare-earth terrain following.
TERRARIUM_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
ZOOM = 12
TILE_SIZE = 256

# Guard rails for the semi-automatic terrain densify.
_DENSIFY_MAX_SAMPLES = 1500       # cap on candidate points sampled per route
_DEFAULT_MAX_WAYPOINTS = 900      # relax the tolerance until the route fits this


def tile_coords(lat, lon, zoom=ZOOM):
    """Return (tile_x, tile_y, pixel_x, pixel_y) for a WGS84 point (the standard
    Web Mercator XYZ tiling used by the Terrarium set)."""
    lat = max(-85.0511, min(85.0511, float(lat)))
    lon = max(-180.0, min(180.0, float(lon)))
    n = 2 ** zoom
    xf = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    yf = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    x, y = int(xf), int(yf)
    px = min(TILE_SIZE - 1, int((xf - x) * TILE_SIZE))
    py = min(TILE_SIZE - 1, int((yf - y) * TILE_SIZE))
    return x, y, px, py


def elevation_from_rgb(r, g, b):
    """Decode a Terrarium tile pixel (R, G, B) to metres above sea level."""
    return (r * 256 + g + b / 256.0) - 32768.0


def _haversine_m(lon1, lat1, lon2, lat2):
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


def sample_elevations(waypoints, sample):
    """Ground elevation (m) at each (lon, lat) waypoint, via the `sample(lon, lat)`
    callable. Used for full-automatic terrain follow, where a waypoint already
    exists at every photo, so no densification is needed."""
    return [sample(lon, lat) for lon, lat in waypoints]


def densify_by_terrain(waypoints, sample, tolerance_m,
                       spacing_m=None, max_points=_DEFAULT_MAX_WAYPOINTS):
    """Semi-automatic terrain follow: insert extra waypoints along the flight
    lines wherever the ground elevation has drifted more than `tolerance_m` from
    the last kept waypoint (each kept waypoint becomes the new reference). The
    short connector hops between lines are left alone.

    `waypoints` is the (lon, lat) turn-point list from the grid planner: two
    points per flight line, snaked, so the legs alternate flight-line / connector
    and the even-indexed legs are the flight lines. `sample(lon, lat)` returns
    ground elevation in metres.

    Returns (waypoints, elevations) as parallel lists. If densifying would exceed
    `max_points`, the tolerance is doubled repeatedly until the route fits (same
    adaptive strategy as the online planner)."""
    pts = list(waypoints)
    if len(pts) < 2:
        return pts, [sample(lon, lat) for lon, lat in pts]

    leg_len = [_haversine_m(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
               for i in range(len(pts) - 1)]
    strip_total = sum(length for i, length in enumerate(leg_len) if i % 2 == 0)
    if spacing_m is None:
        # Tighter tolerances need finer ground sampling to be meaningful.
        spacing_m = min(30.0, max(8.0, float(tolerance_m) * 5.0))
    spacing = max(spacing_m, strip_total / max(1, _DENSIFY_MAX_SAMPLES - len(pts)))

    candidates = []                                  # (lon, lat, is_original)
    for i, length in enumerate(leg_len):
        candidates.append((pts[i][0], pts[i][1], True))
        if i % 2 == 0 and length > spacing:          # only densify the flight lines
            extra = int(length // spacing)
            for j in range(1, extra + 1):
                f = j / (extra + 1)
                candidates.append((pts[i][0] + f * (pts[i + 1][0] - pts[i][0]),
                                   pts[i][1] + f * (pts[i + 1][1] - pts[i][1]),
                                   False))
    candidates.append((pts[-1][0], pts[-1][1], True))
    elevations = [sample(lon, lat) for lon, lat, _ in candidates]

    tolerance = max(0.5, float(tolerance_m))
    for _ in range(6):
        out_pts, out_elevs = [], []
        reference = None
        for (lon, lat, is_orig), elev in zip(candidates, elevations):
            if is_orig:
                out_pts.append((lon, lat))
                out_elevs.append(elev)
                reference = elev
            elif reference is None or abs(elev - reference) > tolerance:
                out_pts.append((lon, lat))
                out_elevs.append(elev)
                reference = elev
        if len(out_pts) <= max_points:
            break
        tolerance *= 2                               # relax until the budget fits
    return out_pts, out_elevs


def heights_above_takeoff(elevations, altitude_m):
    """Per-waypoint executeHeight (relative to the first waypoint, i.e. the launch
    point) that holds a constant `altitude_m` above ground. Because only the
    difference from the first elevation is used, the DEM's vertical datum cancels
    out. Rebase per exported flight by passing that flight's own elevations."""
    if not elevations:
        return []
    base = elevations[0]
    return [round(altitude_m + (elev - base), 1) for elev in elevations]
