"""QGIS adapter for the shared 2D planning engine."""

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
)

from .flypath_engine.grid import (
    find_optimal_direction as _find_optimal_direction,
    generate_grid as _generate_grid,
)
from .flypath_engine.measurements import (
    route_distance_m as _route_distance_m,
    survey_area_m2 as _survey_area_m2,
)
from .grid_route import split_waypoints as split_waypoints


def generate_flight_grid(polygon_geom, polygon_crs, altitude_m,
                         shot_spacing_m, side_overlap, direction_deg,
                         margin_m, drone_specs, densify_spacing=None):
    """Generate WGS84 ``(longitude, latitude)`` flight waypoints."""
    survey_area = _survey_area_wgs84(polygon_geom, polygon_crs)
    result = _generate_grid(
        survey_area,
        altitude_m=altitude_m,
        shot_spacing_m=shot_spacing_m,
        side_overlap=side_overlap,
        direction_deg=direction_deg,
        margin_m=margin_m,
        camera=drone_specs,
        densify_spacing=densify_spacing,
    )
    return ([(point["longitude_deg"], point["latitude_deg"])
             for point in result["waypoints"]],
            result["shot_spacing_m"])


def find_optimal_direction(polygon_geom, polygon_crs, line_spacing_m):
    """Return the shared engine's optimal whole-degree grid direction."""
    try:
        return _find_optimal_direction(
            _survey_area_wgs84(polygon_geom, polygon_crs), line_spacing_m
        )
    except Exception:
        return 0.0


def measure_survey_area(polygon_geom, polygon_crs):
    """Return WGS84 ellipsoidal survey area in square metres."""
    return _survey_area_m2(_survey_area_wgs84(polygon_geom, polygon_crs))


def measure_route(waypoints):
    """Return WGS84 ellipsoidal length for ``(longitude, latitude)`` points."""
    return _route_distance_m([
        {"longitude_deg": longitude, "latitude_deg": latitude}
        for longitude, latitude in waypoints
    ])


def _survey_area_wgs84(polygon_geom, polygon_crs):
    if polygon_geom is None or polygon_geom.isEmpty():
        raise ValueError("Survey polygon is empty.")
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    geometry = QgsGeometry(polygon_geom)
    geometry.transform(QgsCoordinateTransform(
        polygon_crs, wgs84, QgsProject.instance()
    ))
    parts = [_part_to_mapping(part) for part in _polygon_parts(geometry)]
    if not parts:
        raise ValueError("Survey polygon has no exterior ring.")
    return parts[0] if len(parts) == 1 else {"parts": parts}


def _part_to_mapping(rings):
    return {
        "exterior": [_point_to_mapping(point) for point in rings[0]],
        "holes": [
            [_point_to_mapping(point) for point in ring]
            for ring in rings[1:]
        ],
    }


def _point_to_mapping(point):
    return {"latitude_deg": point.y(), "longitude_deg": point.x()}


def _polygon_parts(geometry):
    if geometry.isMultipart():
        return [part for part in geometry.asMultiPolygon() if part]
    polygon = geometry.asPolygon()
    return [polygon] if polygon else []


def _utm_crs_for(longitude, latitude):
    """Return the WGS84 UTM CRS used by corridor and takeoff-zone adapters."""
    zone = min(60, max(1, int((longitude + 180.0) / 6.0) + 1))
    epsg = (32600 if latitude >= 0 else 32700) + zone
    return QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
