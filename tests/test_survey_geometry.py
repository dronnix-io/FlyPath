"""QGIS checks for survey geometry serialization."""

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_survey_geometry_shapes():
    try:
        from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry
        from FlyPath import survey_geometry
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc

    crs = QgsCoordinateReferenceSystem('EPSG:4326')
    polygon = QgsGeometry.fromWkt(
        'POLYGON((10 20,12 20,12 22,10 22,10 20),'
        '(10.5 20.5,11 20.5,11 21,10.5 20.5))')
    area = survey_geometry.planning_area(polygon, crs)
    assert len(area['exterior']) == 4
    assert len(area['holes']) == 1 and len(area['holes'][0]) == 3
    assert survey_geometry.polygon_vertices(polygon, crs) == [
        (10.0, 20.0), (12.0, 20.0), (12.0, 22.0), (10.0, 22.0)]

    line = QgsGeometry.fromWkt('LINESTRING(10 20,11 21)')
    assert survey_geometry.line_vertices(line, crs) == [(10.0, 20.0), (11.0, 21.0)]
    closed_line = QgsGeometry.fromWkt('LINESTRING(10 20,11 21,10 20)')
    assert survey_geometry.line_vertices(closed_line, crs) == [
        (10.0, 20.0), (11.0, 21.0), (10.0, 20.0)]
    multipart = QgsGeometry.fromWkt(
        'MULTILINESTRING((10 20,11 21),(12 22,13 23))')
    try:
        survey_geometry.line_vertices(multipart, crs)
        assert False, 'expected disconnected corridor rejection'
    except survey_geometry.MultipartLineError as exc:
        assert exc.args == (2,)


if __name__ == '__main__':
    test_survey_geometry_shapes()
    print('Survey geometry check passed')
