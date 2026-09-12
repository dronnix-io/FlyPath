"""QGIS- and Django-independent 2D survey-grid generation."""

import math

from pyproj import CRS, Transformer
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import transform

from .route import boustrophedon_route


def generate_grid(survey_area, *, altitude_m, shot_spacing_m, side_overlap,
                  direction_deg, margin_m, camera, densify_spacing=None):
    """Return a JSON-compatible grid from WGS84 survey-area coordinates."""
    if altitude_m <= 0:
        raise ValueError("Altitude must be greater than 0.")
    if not 0 <= side_overlap < 1:
        raise ValueError("Side overlap must be between 0 and 0.99.")
    if margin_m < 0:
        raise ValueError("Margin cannot be negative.")

    metric, inverse = _metric_polygon(survey_area)
    if margin_m:
        metric = metric.buffer(margin_m, quad_segs=8)
    if metric.is_empty:
        raise ValueError("Survey area became empty after applying the margin.")

    focal_length = float(camera["focal_length_mm"])
    sensor_width = float(camera["sensor_width_mm"])
    if focal_length <= 0 or sensor_width <= 0:
        raise ValueError("Camera dimensions must be greater than 0.")
    line_spacing = max(altitude_m * sensor_width / focal_length * (1 - side_overlap), 0.5)
    shot_spacing = max(float(shot_spacing_m), 0.5)

    centre = metric.centroid
    origin = (centre.x, centre.y)
    rotated = affinity.rotate(metric, -direction_deg, origin=origin)
    columns = _scan_columns(rotated, line_spacing, shot_spacing)
    route = boustrophedon_route(columns, densify_spacing)
    if not route:
        raise ValueError("Flight grid produced no waypoints.")

    angle = math.radians(direction_deg)
    waypoints = []
    for x, y in route:
        east, north = _rotate(x, y, *origin, angle)
        longitude, latitude = inverse.transform(east, north)
        waypoints.append({"latitude_deg": latitude, "longitude_deg": longitude})
    return {
        "resolved_direction_deg": direction_deg % 180,
        "line_spacing_m": line_spacing,
        "shot_spacing_m": shot_spacing,
        "waypoints": waypoints,
    }


def find_optimal_direction(survey_area, line_spacing_m):
    """Return the whole-degree grid direction with the shortest route."""
    spacing = line_spacing_m if line_spacing_m and line_spacing_m > 0 else 1.0
    polygon, _inverse = _metric_polygon(survey_area)
    min_x, min_y, max_x, max_y = polygon.bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    step = spacing if not diagonal or diagonal / spacing <= 200 else diagonal / 200

    costs = {degree: _flight_cost(polygon, step, degree)
             for degree in range(180)}
    minimum_segments = min(cost[0] for cost in costs.values())
    candidates = sorted(
        (degree for degree, cost in costs.items() if cost[0] <= minimum_segments + 2),
        key=lambda degree: costs[degree],
    )[:30]
    return float(min(candidates, key=lambda degree: _route_length(polygon, degree, spacing)))


def _metric_polygon(survey_area):
    if not isinstance(survey_area, dict):
        raise ValueError("Survey area must be an object.")
    if "parts" in survey_area:
        parts = survey_area["parts"]
        if not isinstance(parts, list) or not parts:
            raise ValueError("Survey area parts cannot be empty.")
        polygon = MultiPolygon([_polygon(part) for part in parts])
    else:
        polygon = _polygon(survey_area)
    if polygon.is_empty or not polygon.is_valid:
        raise ValueError("Survey area must be a valid polygon.")

    centroid = polygon.centroid
    zone = min(60, max(1, int((centroid.x + 180) / 6) + 1))
    utm = CRS.from_epsg((32600 if centroid.y >= 0 else 32700) + zone)
    forward = Transformer.from_crs("EPSG:4326", utm, always_xy=True)
    inverse = Transformer.from_crs(utm, "EPSG:4326", always_xy=True)
    return transform(forward.transform, polygon), inverse


def _polygon(part):
    if not isinstance(part, dict):
        raise ValueError("Survey area part must be an object.")
    exterior = _ring(part.get("exterior"), "survey_area.exterior")
    holes = [_ring(ring, "survey_area.holes") for ring in part.get("holes", [])]
    return Polygon(exterior, holes)


def _ring(points, field):
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError(f"{field} must contain at least three positions.")
    ring = []
    for point in points:
        try:
            latitude = float(point["latitude_deg"])
            longitude = float(point["longitude_deg"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{field} contains an invalid position.") from None
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError(f"{field} positions must be finite.")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError(f"{field} position is outside WGS84 bounds.")
        ring.append((longitude, latitude))
    return ring


def _scan_columns(polygon, spacing, pad):
    min_x, min_y, max_x, max_y = polygon.bounds
    columns = []
    x = min_x
    while x <= max_x + spacing * 0.5:
        clipped = LineString(((x, min_y - pad), (x, max_y + pad))).intersection(polygon)
        segments = sorted(_line_intervals(clipped))
        columns.append((x, segments))
        x += spacing
    return columns


def _flight_cost(polygon, step, direction_deg):
    centre = polygon.centroid
    rotated = affinity.rotate(polygon, -direction_deg, origin=(centre.x, centre.y))
    low, start, high, end = rotated.bounds
    segments = 0
    offset = low + step / 2
    while offset <= high:
        line = LineString(((offset, start - step), (offset, end + step)))
        segments += len(_line_intervals(line.intersection(rotated)))
        offset += step
    return segments, high - low


def _route_length(polygon, direction_deg, spacing):
    centre = polygon.centroid
    rotated = affinity.rotate(polygon, -direction_deg, origin=(centre.x, centre.y))
    route = boustrophedon_route(_scan_columns(rotated, spacing, spacing))
    return (sum(math.hypot(x2 - x1, y2 - y1)
                for (x1, y1), (x2, y2) in zip(route, route[1:]))
            if len(route) >= 2 else float("inf"))


def _line_intervals(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type in ("LineString", "LinearRing"):
        coordinates = list(geometry.coords)
        if len(coordinates) < 2:
            return []
        low, high = sorted((coordinates[0][1], coordinates[-1][1]))
        return [(low, high)] if high > low else []
    intervals = []
    for part in getattr(geometry, "geoms", ()):
        intervals.extend(_line_intervals(part))
    return intervals


def _rotate(x, y, centre_x, centre_y, angle):
    cosine, sine = math.cos(angle), math.sin(angle)
    dx, dy = x - centre_x, y - centre_y
    return (centre_x + dx * cosine - dy * sine,
            centre_y + dx * sine + dy * cosine)
