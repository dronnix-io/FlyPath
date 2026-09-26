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
    root = project.layerTreeRoot()
    user_group = root.addGroup('FlyPath')
    ordinary = module.QgsVectorLayer('Point?crs=EPSG:4326', 'User layer', 'memory')
    project.addMapLayer(ordinary, False)
    user_group.addLayer(ordinary)
    bridge = project.layerTreeRegistryBridge()
    bridge.setLayerInsertionPoint(user_group, 0)
    missions = [[(13.0, 51.0), (13.1, 51.1), (13.2, 51.2)],
                [(14.0, 52.0), (14.1, 52.1)]]
    ids = module.create(
        missions, heights=[[80, 81, 82], [90, 91]],
        ground=[[100, 101, 102], [110, 111]], project=project)
    try:
        assert len(ids) == 2
        group = root.children()[0]
        assert group is not user_group and group.name() == 'FlyPath'
        assert group.customProperty('flypath_group') is True
        assert [node.layerId() for node in group.children()] == ids[::-1]
        assert [node.layerId() for node in user_group.children()] == [ordinary.id()]
        path, points = [project.mapLayer(layer_id) for layer_id in ids]
        assert path.customProperty('flypath_internal') is True
        assert path.featureCount() == 2 and points.featureCount() == 5
        rows = sorted((feature['mission'], feature['seq'], feature['wp_type'],
                       feature['ground_elevation_m'], feature['flight_height_m'])
                      for feature in points.getFeatures())
        assert rows[0] == (0, 1, 'start', 100.0, 80.0)
        assert rows[2] == (0, 3, 'end', 102.0, 82.0)
        assert rows[3][2] == 'start' and rows[4][2] == 'end'
        path_rules = path.renderer().rootRule().children()
        assert len(path_rules) == 2
        assert [rule.label() for rule in path_rules] == ['Flight 1', 'Flight 2']
        assert all(rule.symbol().symbolLayerCount() == 2 for rule in path_rules)
        assert all(rule.symbol().symbolLayer(0).color().name().upper() == '#071018'
                   for rule in path_rules)
        assert [rule.symbol().symbolLayer(1).color().name().upper()
                for rule in path_rules] == ['#FFE600', '#FF9F43']
        point_rules = points.renderer().rootRule().children()
        assert point_rules[0].symbol().color().name().upper() == module.START_COLOR
        assert point_rules[1].symbol().color().name().upper() == module.END_COLOR
        assert point_rules[2].symbol().color().name().upper() == module.MID_COLOR

        module.remove_stale(project)
        assert project.mapLayer(ordinary.id()) is ordinary
        assert not any(project.mapLayer(layer_id) for layer_id in ids), \
            'Plugin startup must remove stale FlyPath layers restored by QGIS'

        replacement = module.redraw(ids, [missions[0]], project=project)
        assert replacement != ids
        assert root.children()[0] is group
        assert [node.layerId() for node in group.children()] == replacement[::-1]
        assert all(project.mapLayer(layer_id) for layer_id in replacement)
        assert project.mapLayer(replacement[0]).featureCount() == 1
        group.children()[0].setItemVisibilityChecked(False)
        user_parent = root.insertGroup(0, 'User parent')
        assert module.redraw(replacement, [missions[0]], project=project) == replacement
        group = root.children()[0]
        assert group.customProperty('flypath_group') is True
        assert not group.children()[0].itemVisibilityChecked()
        assert all(project.mapLayer(layer_id) for layer_id in replacement)
        user_parent.addChildNode(group.clone())
        root.removeChildNode(group)
        extra = module.QgsVectorLayer('Point?crs=EPSG:4326', 'FlyPath extra', 'memory')
        extra.setCustomProperty('flypath_internal', True)
        extra_id = extra.id()
        module.register(extra, project)
        app.processEvents()
        group = root.children()[0]
        assert group.customProperty('flypath_group') is True
        assert not user_parent.children()
        assert [node.layerId() for node in group.children()] == replacement[::-1] + [extra_id]
        assert not group.children()[0].itemVisibilityChecked()
        assert all(project.mapLayer(layer_id) for layer_id in replacement)
        assert project.mapLayer(extra_id) is extra
        assert project.mapLayer(ordinary.id()) is ordinary
        module.remove(replacement, project)
        assert not any(project.mapLayer(layer_id) for layer_id in replacement)
        module.remove_stale(project)
        assert project.mapLayer(extra_id) is None
        root.removeChildNode(group)
        recreated = module.create([missions[0]], project=project)
        assert root.children()[0].customProperty('flypath_group') is True
        assert [node.layerId() for node in root.children()[0].children()] == recreated[::-1]
        assert project.mapLayer(ordinary.id()) is ordinary
    finally:
        bridge.setLayerInsertionPoint(root, 0)
        project.clear()


if __name__ == '__main__':
    test_preview_layers_create_redraw_and_recover()
    print('preview layer tests passed')
