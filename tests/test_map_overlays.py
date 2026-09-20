"""Installed-QGIS checks for takeoff and contour overlay ownership."""

import importlib
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_takeoff_and_contour_layers_are_registered_styled_and_removed():
    try:
        from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,
                               QgsCoordinateTransform, QgsProject)
        package = Path(__file__).resolve().parents[1].name
        overlays = importlib.import_module(package + '.map_overlays')
        previews = importlib.import_module(package + '.preview_layers')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    project = QgsProject.instance()
    project.clear()
    preview_ids = previews.create([[(13.0, 51.0), (13.1, 51.1)]], project=project)
    crs = QgsCoordinateReferenceSystem('EPSG:4326')
    identity = QgsCoordinateTransform(crs, crs, project)
    takeoff = overlays.create_takeoff({
        'radius_m': .01, 'to_wgs': identity,
        'zones': [
            {'index': 0, 'center_utm': (13.0, 51.0), 'flat': True,
             'cells_utm': [], 'spacing': .001, 'ref_elev': 123.456},
            {'index': 1, 'center_utm': (13.1, 51.1), 'flat': False,
             'cells_utm': [(13.1, 51.1)], 'spacing': .002, 'ref_elev': 130},
        ],
    }, preview_ids, project)
    contours = overlays.create_contours({
        'segments': [(120, ((13.0, 51.0), (13.1, 51.1))),
                     (120, ((13.1, 51.1), (13.2, 51.2))),
                     (130, ((13.0, 51.1), (13.1, 51.2)))],
    }, preview_ids, project)
    try:
        assert takeoff.featureCount() == 2
        assert sorted(feature['ref_elevation_m']
                      for feature in takeoff.getFeatures()) == [123.5, 130.0]
        assert len(takeoff.renderer().rootRule().children()) == 2
        assert contours.featureCount() == 2
        assert contours.labelsEnabled() and contours.labeling() is not None
        assert contours.renderer().symbol().color().name().upper() == '#4DA3FF'
        assert takeoff.customProperty('flypath_internal') is True
        assert contours.customProperty('flypath_internal') is True
        tree_ids = [node.layerId() for node in project.layerTreeRoot().children()]
        assert tree_ids.index(takeoff.id()) > tree_ids.index(preview_ids[-1])
        assert tree_ids.index(contours.id()) > tree_ids.index(preview_ids[-1])
        takeoff_id, contour_id = takeoff.id(), contours.id()
        overlays.remove(takeoff_id, project)
        overlays.remove(contour_id, project)
        assert project.mapLayer(takeoff_id) is None
        assert project.mapLayer(contour_id) is None
    finally:
        project.clear()


if __name__ == '__main__':
    test_takeoff_and_contour_layers_are_registered_styled_and_removed()
    print('map overlay tests passed')
