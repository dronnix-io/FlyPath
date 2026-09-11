"""QGIS-runtime check for imported mission centering, CRS and padding."""
import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_website_map_extent():
    try:
        from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry
        from qgis.gui import QgsMapCanvas
        module = importlib.import_module(Path(__file__).resolve().parents[1].name + '.flypath_dialog')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    canvas = QgsMapCanvas()
    canvas.resize(800, 600)
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
    planner = SimpleNamespace(iface=SimpleNamespace(mapCanvas=lambda: canvas))
    source_crs = QgsCoordinateReferenceSystem('EPSG:4326')
    for wkt in (
        'POLYGON((13 52,13.01 52,13.01 52.01,13 52.01,13 52))',
        'LINESTRING(13 52,13 52.01)',
        'LINESTRING(13 52,13.01 52)',
    ):
        module.FlyPathDialog._zoom_to_website_geometry(
            planner, QgsGeometry.fromWkt(wkt), source_crs)
        extent = canvas.extent()
        assert extent.width() > 0 and extent.height() > 0
        # Berlin in Web Mercator; fails if raw longitude/latitude is used.
        assert 1447000 < extent.center().x() < 1449000
        assert 6799000 < extent.center().y() < 6803000
        assert extent.width() > 1100 or extent.height() > 2100
    canvas.close()
    app.processEvents()


if __name__ == '__main__':
    test_website_map_extent()
    print('Website map extent check passed')
