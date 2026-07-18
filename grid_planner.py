"""
grid_planner.py
---------------
Lawnmower flight-grid generator for 2D orthomosaic mapping.

Public API
----------
generate_flight_grid(...)  -> (waypoints, shot_spacing_m)
    waypoints      : list of (lon, lat) — turn points only (line endpoints)
    shot_spacing_m : float — camera trigger interval in metres for multipleDistance

find_optimal_direction(...) -> float degrees
"""

import math

from qgis.core import (
    QgsGeometry,
    QgsPointXY,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsWkbTypes,
)


# ── Public functions ──────────────────────────────────────────────────────────

def generate_flight_grid(polygon_geom, polygon_crs, altitude_m,
                          shot_spacing_m, side_overlap, direction_deg,
                          margin_m, drone_specs):
    """
    Generate a lawnmower (boustrophedon) flight grid for 2D mapping.

    Returns only the turn points (start and end of each flight line),
    not every photo-trigger position. The camera is fired by a
    multipleDistance trigger in the WPML file at shot_spacing_m intervals.

    Parameters
    ----------
    polygon_geom   : QgsGeometry  — survey area polygon (any CRS)
    polygon_crs    : QgsCoordinateReferenceSystem
    altitude_m     : float        — AGL flight altitude in metres
    shot_spacing_m : float        — along-track distance between photos (speed × interval)
    side_overlap   : float        — 0.0–1.0
    direction_deg : float        — flight-line direction, degrees CW from North
    margin_m      : float        — buffer to add around polygon (metres)
    drone_specs   : dict         — entry from DRONE_SPECS in flypath_dialog.py

    Returns
    -------
    (waypoints, shot_spacing_m)
    waypoints      : list of (longitude, latitude) tuples in WGS84 — turn points only
    shot_spacing_m : float — along-track photo interval in metres
    Raises
    ------
    ValueError  if inputs are invalid or the polygon produces no waypoints
    """
    if altitude_m <= 0:
        raise ValueError('Altitude must be greater than 0.')
    if not (0.0 <= side_overlap < 1.0):
        raise ValueError('Side overlap must be between 0% and 99%.')
    if not drone_specs:
        raise ValueError('No drone specifications provided.')

    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')

    # 1 ── Reproject polygon to WGS84 to locate the UTM zone
    to_wgs84 = QgsCoordinateTransform(polygon_crs, wgs84, QgsProject.instance())
    poly_wgs84 = QgsGeometry(polygon_geom)
    poly_wgs84.transform(to_wgs84)
    centroid_wgs84 = poly_wgs84.centroid().asPoint()

    # 2 ── Pick a metric UTM CRS centred on the polygon
    utm_crs = _utm_crs_for(centroid_wgs84.x(), centroid_wgs84.y())

    # 3 ── Reproject polygon to UTM (metres)
    to_utm = QgsCoordinateTransform(polygon_crs, utm_crs, QgsProject.instance())
    poly_utm = QgsGeometry(polygon_geom)
    poly_utm.transform(to_utm)

    # 4 ── Apply survey margin
    if margin_m > 0:
        poly_utm = poly_utm.buffer(margin_m, 8)

    if poly_utm.isEmpty() or poly_utm.isNull():
        raise ValueError(
            'Survey polygon became empty after applying the margin.\n'
            'Reduce the margin value or use a larger survey area.'
        )

    # 5 ── Camera footprint and spacing (all in metres)
    fl = drone_specs['focal_length_mm']
    sw = drone_specs['sensor_width_mm']

    footprint_across = altitude_m * sw / fl          # perpendicular to flight

    line_spacing = max(footprint_across * (1.0 - side_overlap), 0.5)
    shot_spacing = max(shot_spacing_m, 0.5)

    # 6 ── Rotate polygon so the flight direction aligns with +Y
    centroid_utm = poly_utm.centroid().asPoint()
    cx, cy = centroid_utm.x(), centroid_utm.y()
    angle_rad = math.radians(direction_deg)

    exterior = _exterior_ring(poly_utm)
    if not exterior:
        raise ValueError('Survey polygon has no exterior ring — check the polygon geometry.')

    rot_pts = [_rotate(pt.x(), pt.y(), cx, cy, -angle_rad) for pt in exterior]
    rotated_poly = QgsGeometry.fromPolygonXY(
        [[QgsPointXY(x, y) for x, y in rot_pts]]
    )

    bbox = rotated_poly.boundingBox()
    x_start = bbox.xMinimum()
    x_end   = bbox.xMaximum()
    y_lo    = bbox.yMinimum() - shot_spacing
    y_hi    = bbox.yMaximum() + shot_spacing

    # 7 ── Sweep scan lines — collect only the turn points (line endpoints)
    turn_pts_rot = []
    line_idx = 0
    x = x_start

    while x <= x_end + line_spacing * 0.5:
        scan = QgsGeometry.fromPolylineXY([
            QgsPointXY(x, y_lo),
            QgsPointXY(x, y_hi),
        ])
        clipped = scan.intersection(rotated_poly)

        if not clipped.isEmpty() and not clipped.isNull():
            segments = _line_segments(clipped)
            # Collect only the start and end of each segment (turn points)
            line_turns = []
            for (x1, y1), (x2, y2) in segments:
                line_turns.extend([(x1, y1), (x2, y2)])
            if line_idx % 2 == 1:
                line_turns = line_turns[::-1]   # reverse alternate lines → snake
            turn_pts_rot.extend(line_turns)

        x += line_spacing
        line_idx += 1

    if not turn_pts_rot:
        raise ValueError(
            'Flight grid produced no waypoints.\n'
            'Try a larger survey area, lower side overlap, or a smaller margin.'
        )

    # 8 ── Rotate turn points back to UTM orientation
    waypoints_utm = [_rotate(px, py, cx, cy, angle_rad) for px, py in turn_pts_rot]

    # 9 ── Transform UTM → WGS84
    from_utm = QgsCoordinateTransform(utm_crs, wgs84, QgsProject.instance())
    result = []
    for px, py in waypoints_utm:
        pt = from_utm.transform(QgsPointXY(px, py))
        result.append((pt.x(), pt.y()))        # (lon, lat)

    return result, shot_spacing


def find_optimal_direction(polygon_geom, polygon_crs, line_spacing_m):
    """
    Return the flight direction (degrees CW from North) that minimises the
    estimated mission flight time for the actual polygon shape.

    The total length of the flight lines needed to cover an area is roughly
    (area / line_spacing) regardless of direction, so flight time is dominated
    by the number of passes and turns. This routine picks the direction that
    yields the fewest flight-line segments inside the real polygon at the given
    line spacing. Counting real segments (not the convex-hull width) means
    concave, L-shaped or irregular areas, where a single pass is split into
    several segments across a notch, are handled correctly. Ties are broken by
    the smaller perpendicular span (tighter alignment with the long axis).

    A coarse-to-fine angle search (every 5 deg, then refined to 1 deg around the
    best) keeps it fast. Uses only QgsGeometry APIs common to QGIS 3 and 4.

    Parameters
    ----------
    polygon_geom   : QgsGeometry (any CRS)
    polygon_crs    : QgsCoordinateReferenceSystem
    line_spacing_m : float, perpendicular spacing between flight lines in metres

    Returns
    -------
    float : best direction in degrees (0 to 179)
    """
    try:
        spacing = line_spacing_m if (line_spacing_m and line_spacing_m > 0) else 1.0

        wgs84    = QgsCoordinateReferenceSystem('EPSG:4326')
        to_wgs84 = QgsCoordinateTransform(polygon_crs, wgs84, QgsProject.instance())
        poly_wgs84 = QgsGeometry(polygon_geom)
        poly_wgs84.transform(to_wgs84)
        centroid = poly_wgs84.centroid().asPoint()

        utm_crs  = _utm_crs_for(centroid.x(), centroid.y())
        to_utm   = QgsCoordinateTransform(polygon_crs, utm_crs, QgsProject.instance())
        poly_utm = QgsGeometry(polygon_geom)
        poly_utm.transform(to_utm)

        pts = [(v.x(), v.y()) for v in poly_utm.vertices()]
        if len(pts) < 3:
            return 0.0

        # One scan step for the whole search so segment counts stay comparable
        # across angles; cap total scan lines (~200) to stay fast on big areas.
        bb   = poly_utm.boundingBox()
        diag = math.hypot(bb.width(), bb.height())
        step = spacing if (diag <= 0 or diag / spacing <= 200) else diag / 200.0

        coarse = _best_angle(poly_utm, pts, step, range(0, 180, 5))
        lo, hi = int(coarse) - 4, int(coarse) + 4
        fine   = _best_angle(poly_utm, pts, step,
                             [a % 180 for a in range(lo, hi + 1)])
        return float(fine % 180)
    except Exception:
        return 0.0


def _best_angle(poly_utm, pts, step, angles):
    """Return the angle (deg) in `angles` with the lowest (segments, span) cost."""
    best_angle = 0.0
    best_cost  = None
    for deg in angles:
        cost = _flight_cost(poly_utm, pts, step, float(deg))
        if best_cost is None or cost < best_cost:
            best_cost  = cost
            best_angle = float(deg)
    return best_angle


def _flight_cost(poly_utm, pts, step, deg):
    """
    Relative flight cost for flight lines oriented at `deg`.

    Returns (segment_count, perpendicular_span): fewer segments means fewer
    turns and shorter flight time; span breaks ties. Scan lines are cast along
    the flight direction at `step` spacing and intersected with the real
    polygon, so a concavity that splits a pass into pieces is counted.
    """
    rad = math.radians(deg)
    # Match generate_flight_grid's convention: at `deg` the line-spacing
    # (perpendicular) axis is u and the flight passes run along v.
    ux, uy = math.cos(rad), math.sin(rad)     # across / line-spacing axis
    vx, vy = -math.sin(rad), math.cos(rad)    # along-track (pass) axis

    t_vals = [x * ux + y * uy for x, y in pts]   # perpendicular offsets
    s_vals = [x * vx + y * vy for x, y in pts]   # along-track positions
    t_min, t_max = min(t_vals), max(t_vals)
    s_min, s_max = min(s_vals), max(s_vals)
    span = t_max - t_min

    pad = step
    a0, a1 = s_min - pad, s_max + pad
    seg_count = 0
    t = t_min + step / 2.0
    while t <= t_max:
        p0 = QgsPointXY(a0 * vx + t * ux, a0 * vy + t * uy)
        p1 = QgsPointXY(a1 * vx + t * ux, a1 * vy + t * uy)
        inter = poly_utm.intersection(QgsGeometry.fromPolylineXY([p0, p1]))
        if inter and not inter.isEmpty():
            seg_count += len(_polylines(inter))
        t += step

    return (seg_count, span)


def _polylines(geom):
    """Return the list of polylines in a (possibly multi) line geometry."""
    line = geom.asPolyline()
    if line:
        return [line]
    multi = geom.asMultiPolyline()
    if multi:
        return multi
    return []


# ── Private helpers ───────────────────────────────────────────────────────────

def _utm_crs_for(lon, lat):
    """Return the WGS84 UTM CRS appropriate for the given lon/lat."""
    zone = int((lon + 180.0) / 6.0) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return QgsCoordinateReferenceSystem(f'EPSG:{epsg}')


def _rotate(x, y, cx, cy, angle_rad):
    """Rotate point (x, y) around centre (cx, cy) by angle_rad."""
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    dx, dy = x - cx, y - cy
    return (cx + dx * cos_a - dy * sin_a,
            cy + dx * sin_a + dy * cos_a)


def _exterior_ring(geom):
    """Return the exterior ring of a polygon geometry as a list of QgsPointXY, or None."""
    if geom is None or geom.isEmpty():
        return None
    poly = geom.asPolygon()
    if poly:
        return poly[0]
    multi = geom.asMultiPolygon()
    if multi and multi[0]:
        return multi[0][0]
    return None


def _line_segments(geom):
    """
    Extract a list of ((x1, y1), (x2, y2)) endpoint pairs from a line geometry.
    Handles both LineString and MultiLineString.
    """
    result = []
    if geom.isEmpty():
        return result
    if QgsWkbTypes.geometryType(geom.wkbType()) != QgsWkbTypes.LineGeometry:
        return result

    if geom.isMultipart():
        parts = geom.asMultiPolyline()
    else:
        parts = [geom.asPolyline()]

    for part in parts:
        if len(part) >= 2:
            result.append(
                ((part[0].x(),  part[0].y()),
                 (part[-1].x(), part[-1].y()))
            )
    return result
