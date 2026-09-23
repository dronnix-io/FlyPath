"""Translate QGIS survey geometry into FlyPath's WGS84 consumer shapes."""

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
)


class MultipartLineError(ValueError):
    pass


def planning_area(polygon, crs):
    """Return the engine survey-area shape, retaining polygon parts and holes."""
    geometry = _wgs84(polygon, crs)
    if geometry is None:
        return None
    polygons = (geometry.asMultiPolygon() if geometry.isMultipart()
                else [geometry.asPolygon()])
    parts = [
        {'exterior': _engine_ring(part[0]),
         'holes': [_engine_ring(interior) for interior in part[1:]]}
        for part in polygons if part and part[0]
    ]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {'parts': parts}


def polygon_vertices(polygon, crs):
    """Return the first exterior polygon ring as unclosed (lon, lat) pairs."""
    geometry = _wgs84(polygon, crs)
    if geometry is None:
        return None
    if geometry.isMultipart():
        parts = geometry.asMultiPolygon()
        ring = parts[0][0] if parts and parts[0] else None
    else:
        polygon = geometry.asPolygon()
        ring = polygon[0] if polygon else None
    return _pairs(ring) if ring else None


def line_vertices(line, crs):
    """Return one line as (lon, lat) pairs; reject disconnected multipart lines."""
    geometry = _wgs84(line, crs)
    if geometry is None:
        return None
    parts = geometry.asMultiPolyline() if geometry.isMultipart() else [geometry.asPolyline()]
    parts = [part for part in parts if part]
    if not parts:
        return None
    if len(parts) > 1:
        raise MultipartLineError(len(parts))
    return [(point.x(), point.y()) for point in parts[0]]


def _wgs84(geometry, crs):
    if geometry is None or crs is None:
        return None
    result = QgsGeometry(geometry)
    result.transform(QgsCoordinateTransform(
        crs, QgsCoordinateReferenceSystem('EPSG:4326'), QgsProject.instance()))
    return result


def _engine_ring(points):
    pairs = _pairs(points)
    return [{'latitude_deg': latitude, 'longitude_deg': longitude}
            for longitude, latitude in pairs]


def _pairs(points):
    coordinates = [(point.x(), point.y()) for point in points]
    if len(coordinates) > 1 and coordinates[0] == coordinates[-1]:
        coordinates.pop()
    return coordinates
