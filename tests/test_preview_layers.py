"""Installed-QGIS checks for route preview layer ownership and attributes."""

import importlib
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_preview_layers_create_redraw_and_recover():
    try:
        from qgis.core import QgsApplication, QgsProject
        module = importlib.import_module(
            Path(__file__).resolve().parents[1].name + '.preview_layers')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    project = QgsProject.instance()
    project.clear()
    missions = [[(13.0, 51.0), (13.1, 51.1), (13.2, 51.2)],
                [(14.0, 52.0), (14.1, 52.1)]]
    ids = module.create(
        missions, heights=[[80, 81, 82], [90, 91]],
        ground=[[100, 101, 102], [110, 111]], project=project)
    try:
        assert len(ids) == 2
        path, points = [project.mapLayer(layer_id) for layer_id in ids]
        assert path.customProperty('flypath_internal') is True
        assert path.featureCount() == 2 and points.featureCount() == 5
        rows = sorted((feature['mission'], feature['seq'], feature['wp_type'],
                       feature['ground_elevation_m'], feature['flight_height_m'])
                      for feature in points.getFeatures())
        assert rows[0] == (0, 1, 'start', 100.0, 80.0)
        assert rows[2] == (0, 3, 'end', 102.0, 82.0)
        assert rows[3][2] == 'start' and rows[4][2] == 'end'
        assert len(path.renderer().rootRule().children()) == 2

        ordinary = module.QgsVectorLayer('Point?crs=EPSG:4326', 'User layer', 'memory')
        project.addMapLayer(ordinary)
        module.remove_stale(project)
        assert project.mapLayer(ordinary.id()) is ordinary
        assert not any(project.mapLayer(layer_id) for layer_id in ids), \
            'Plugin startup must remove stale FlyPath layers restored by QGIS'

        replacement = module.redraw(ids, [missions[0]], project=project)
        assert replacement != ids
        assert all(project.mapLayer(layer_id) for layer_id in replacement)
        assert project.mapLayer(replacement[0]).featureCount() == 1
        module.remove(replacement, project)
        assert not any(project.mapLayer(layer_id) for layer_id in replacement)
    finally:
        project.clear()


if __name__ == '__main__':
    test_preview_layers_create_redraw_and_recover()
    print('preview layer tests passed')
