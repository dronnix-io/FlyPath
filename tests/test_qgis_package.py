"""Smoke-test the built plugin ZIP in the supported QGIS runtime."""

import argparse
from pathlib import Path
import sys
import tempfile
import zipfile


def main(archive_path):
    from qgis.core import (
        Qgis,
        QgsApplication,
        QgsCoordinateReferenceSystem,
        QgsGeometry,
        QgsPointXY,
    )

    assert Qgis.QGIS_VERSION.startswith("3.44."), Qgis.QGIS_VERSION
    with tempfile.TemporaryDirectory() as temp:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(temp)
        sys.path.insert(0, temp)
        from FlyPath import grid_planner
        from FlyPath.flypath_engine import __version__ as engine_version
        import pyproj
        import shapely

        assert engine_version == "1.0.0"
        assert tuple(map(int, pyproj.__version__.split(".")[:2])) >= (3, 7)
        assert tuple(map(int, shapely.__version__.split(".")[:2])) >= (2, 1)

        app = QgsApplication([], False)
        app.initQgis()
        try:
            geometry = QgsGeometry.fromPolygonXY([[
                QgsPointXY(8.0, 51.0), QgsPointXY(8.002, 51.0),
                QgsPointXY(8.002, 51.002), QgsPointXY(8.0, 51.002),
                QgsPointXY(8.0, 51.0),
            ]])
            route, spacing = grid_planner.generate_flight_grid(
                geometry, QgsCoordinateReferenceSystem("EPSG:4326"),
                80, 16, 0.7, 70, 0,
                {"sensor_width_mm": 9.6, "sensor_height_mm": 7.2,
                 "focal_length_mm": 6.7, "image_width_px": 4032,
                 "image_height_px": 3024},
            )
            assert route and spacing == 16
        finally:
            app.exitQgis()

    print("PASS  packaged plugin runs with QGIS, Shapely and pyproj")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    main(parser.parse_args().archive)
