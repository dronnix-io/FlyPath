"""Ellipsoidal measurements shared by the website and QGIS plugin."""

import math

from pyproj import Geod


_WGS84 = Geod(ellps="WGS84")


def survey_area_m2(survey_area):
    """Return WGS84 ellipsoidal polygon area, subtracting interior holes."""
    if not isinstance(survey_area, dict):
        raise ValueError("Survey area must be an object.")
    if "parts" in survey_area:
        parts = survey_area["parts"]
        if not isinstance(parts, list) or not parts:
            raise ValueError("Survey area parts cannot be empty.")
        return sum(survey_area_m2(part) for part in parts)
    area = abs(_ring_area(survey_area.get("exterior")))
    area -= sum(abs(_ring_area(hole)) for hole in survey_area.get("holes", []))
    if area <= 0:
        raise ValueError("Survey area must have positive ellipsoidal area.")
    return area


def route_distance_m(waypoints):
    """Return WGS84 ellipsoidal length through ordered waypoint objects."""
    points = [_position(point) for point in waypoints]
    distance = 0.0
    for (longitude1, latitude1), (longitude2, latitude2) in zip(points, points[1:]):
        _azimuth1, _azimuth2, segment = _WGS84.inv(
            longitude1, latitude1, longitude2, latitude2
        )
        distance += segment
    return distance


def _ring_area(points):
    ring = [_position(point) for point in points or []]
    if len(ring) < 3:
        raise ValueError("A polygon ring requires at least three positions.")
    longitudes, latitudes = zip(*ring)
    area, _perimeter = _WGS84.polygon_area_perimeter(longitudes, latitudes)
    return area


def _position(point):
    try:
        latitude = float(point["latitude_deg"])
        longitude = float(point["longitude_deg"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid WGS84 position.") from None
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("WGS84 positions must be finite.")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Position is outside WGS84 bounds.")
    return longitude, latitude
