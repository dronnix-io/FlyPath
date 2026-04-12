"""
grid_planner.py
---------------
Lawnmower flight-grid generator for 2D orthomosaic mapping.

Public API
----------
generate_flight_grid(...)  -> list of (lon, lat) in WGS84
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
                          front_overlap, side_overlap, direction_deg,
                          margin_m, drone_specs):
    """
    Generate a lawnmower (boustrophedon) flight grid for 2D mapping.

    Parameters
    ----------
    polygon_geom  : QgsGeometry  — survey area polygon (any CRS)
    polygon_crs   : QgsCoordinateReferenceSystem
    altitude_m    : float        — AGL flight altitude in metres
    front_overlap : float        — 0.0–1.0  (e.g. 0.80 for 80 %)
    side_overlap  : float        — 0.0–1.0
    direction_deg : float        — flight-line direction, degrees clockwise from North
    margin_m      : float        — buffer to add around polygon (metres)
    drone_specs   : dict         — entry from DRONE_SPECS in flypath_dialog.py

    Returns
    -------
    list of (longitude, latitude) tuples in WGS84, ordered as a snake path.
    Returns an empty list on failure.
    """
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
        return []

    # 5 ── Camera footprint and spacing (all in metres)
    fl = drone_specs['focal_length_mm']
    sw = drone_specs['sensor_width_mm']
    sh = drone_specs['sensor_height_mm']

    footprint_across = altitude_m * sw / fl          # perpendicular to flight
    footprint_along  = altitude_m * sh / fl          # along flight direction

    line_spacing = max(footprint_across * (1.0 - side_overlap),  0.5)
    shot_spacing = max(footprint_along  * (1.0 - front_overlap), 0.5)

    # 6 ── Rotate polygon so the flight direction aligns with +Y
    centroid_utm = poly_utm.centroid().asPoint()
    cx, cy = centroid_utm.x(), centroid_utm.y()
    angle_rad = math.radians(direction_deg)

    exterior = _exterior_ring(poly_utm)
    if not exterior:
        return []

    rot_pts = [_rotate(pt.x(), pt.y(), cx, cy, -angle_rad) for pt in exterior]
    rotated_poly = QgsGeometry.fromPolygonXY(
        [[QgsPointXY(x, y) for x, y in rot_pts]]
    )

    bbox = rotated_poly.boundingBox()
    x_start = bbox.xMinimum()
    x_end   = bbox.xMaximum()
    y_lo    = bbox.yMinimum() - shot_spacing
    y_hi    = bbox.yMaximum() + shot_spacing

    # 7 ── Sweep scan lines, clip to polygon, collect waypoints
    waypoints_rot = []
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
            for (x1, y1), (x2, y2) in segments:
                pts = _sample_segment(x1, y1, x2, y2, shot_spacing)
                if line_idx % 2 == 1:
                    pts = pts[::-1]            # reverse alternate lines → snake
                waypoints_rot.extend(pts)

        x += line_spacing
        line_idx += 1

    if not waypoints_rot:
        return []

    # 8 ── Rotate waypoints back to UTM orientation
    waypoints_utm = [_rotate(px, py, cx, cy, angle_rad) for px, py in waypoints_rot]

    # 9 ── Transform UTM → WGS84
    from_utm = QgsCoordinateTransform(utm_crs, wgs84, QgsProject.instance())
    result = []
    for px, py in waypoints_utm:
        pt = from_utm.transform(QgsPointXY(px, py))
        result.append((pt.x(), pt.y()))        # (lon, lat)

    return result


def find_optimal_direction(polygon_geom, polygon_crs, line_spacing_m):
    """
    Return the flight direction (degrees CW from North) that minimises the
    number of flight lines, i.e. aligns lines with the polygon's longest axis.

    Tries every integer degree from 0–179 and picks the one with the smallest
    perpendicular span.

    Parameters
    ----------
    polygon_geom   : QgsGeometry (any CRS)
    polygon_crs    : QgsCoordinateReferenceSystem
    line_spacing_m : float — line spacing in metres (used only for tie-breaking)

    Returns
    -------
    float : best direction in degrees
    """
    wgs84   = QgsCoordinateReferenceSystem('EPSG:4326')
    to_wgs84 = QgsCoordinateTransform(polygon_crs, wgs84, QgsProject.instance())
    poly_wgs84 = QgsGeometry(polygon_geom)
    poly_wgs84.transform(to_wgs84)
    centroid = poly_wgs84.centroid().asPoint()

    utm_crs = _utm_crs_for(centroid.x(), centroid.y())
    to_utm   = QgsCoordinateTransform(polygon_crs, utm_crs, QgsProject.instance())
    poly_utm = QgsGeometry(polygon_geom)
    poly_utm.transform(to_utm)

    hull = poly_utm.convexHull()
    exterior = _exterior_ring(hull) or _exterior_ring(poly_utm)
    if not exterior:
        return 0.0

    xs = [pt.x() for pt in exterior]
    ys = [pt.y() for pt in exterior]

    best_span  = float('inf')
    best_angle = 0.0

    for deg in range(180):
        rad   = math.radians(deg)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        # Project each vertex onto the axis perpendicular to the flight direction
        projections = [-x * sin_a + y * cos_a for x, y in zip(xs, ys)]
        span = max(projections) - min(projections)
        if span < best_span:
            best_span  = span
            best_angle = float(deg)

    return best_angle


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


def _sample_segment(x1, y1, x2, y2, spacing):
    """
    Return a list of (x, y) points sampled along (x1,y1)→(x2,y2) at `spacing` intervals.
    Always includes the start and end points.
    """
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 1e-6:
        return [(x1, y1)]

    dx = (x2 - x1) / length
    dy = (y2 - y1) / length

    pts = []
    d = 0.0
    while d < length - spacing * 0.01:
        pts.append((x1 + dx * d, y1 + dy * d))
        d += spacing

    # Always close with the actual endpoint
    if not pts or math.hypot(pts[-1][0] - x2, pts[-1][1] - y2) > spacing * 0.05:
        pts.append((x2, y2))

    return pts
