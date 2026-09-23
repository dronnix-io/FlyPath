"""QGIS adapter for computing takeoff-zone samples from planned flights."""

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
)

from .grid_planner import _utm_crs_for
from .takeoff_zone import gsd_variance_pct, sample_grid, search_bounds, takeoff_zone


def compute_zones(parts, sample_elevation, missing_error, *, tolerance_m,
                  altitude_m, same_takeoff, radius_m=500.0, steps=40):
    """Sample and return takeoff zones for non-empty planned flight parts."""
    parts = [part for part in (parts or []) if part]
    if not parts:
        return None
    if same_takeoff:
        parts = parts[:1]
    spacing = max(10.0, (2.0 * radius_m) / steps)
    origin_lon, origin_lat = parts[0][0]
    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
    utm = _utm_crs_for(origin_lon, origin_lat)
    to_utm = QgsCoordinateTransform(wgs84, utm, QgsProject.instance())
    to_wgs = QgsCoordinateTransform(utm, wgs84, QgsProject.instance())

    zones = []
    sample_count = 0
    for index, mission in enumerate(parts):
        longitude, latitude = mission[0]
        reference = sample_elevation(longitude, latitude)
        point = to_utm.transform(QgsPointXY(longitude, latitude))
        center = point.x(), point.y()
        candidates = []
        disk_count = 0
        for x, y in sample_grid(search_bounds([center], radius_m), spacing):
            if math.hypot(x - center[0], y - center[1]) > radius_m:
                continue
            position = to_wgs.transform(QgsPointXY(x, y))
            try:
                elevation = sample_elevation(position.x(), position.y())
            except missing_error:
                continue
            candidates.append((x, y, elevation))
            disk_count += 1
        sample_count += len(candidates)
        accepted = takeoff_zone(
            candidates, [center], reference, tolerance_m, radius_m)
        cells = [(x, y) for x, y, _elevation in accepted]
        zones.append({
            'index': index,
            'ref_elev': reference,
            'center_utm': center,
            'spacing': spacing,
            'flat': disk_count > 0 and len(cells) == disk_count,
            'cells_utm': cells,
        })
    return {
        'zones': zones,
        'gsd_var': gsd_variance_pct(tolerance_m, altitude_m),
        'n_sampled': sample_count,
        'to_wgs': to_wgs,
        'radius_m': radius_m,
    }
