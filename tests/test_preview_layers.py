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
        group = root.children()[0]
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
        assert not any(node.customProperty('flypath_group') is True
                       for node in root.children())
        recreated = module.create([missions[0]], project=project)
        assert root.children()[0].customProperty('flypath_group') is True
        assert [node.layerId() for node in root.children()[0].children()] == recreated[::-1]
        assert project.mapLayer(ordinary.id()) is ordinary
    finally:
        bridge.setLayerInsertionPoint(root, 0)
        project.clear()


def test_order_visibility_and_group_cleanup():
    try:
        from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
        module = importlib.import_module(
            Path(__file__).resolve().parents[1].name + '.preview_layers')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    project = QgsProject.instance()
    project.clear()
    root = project.layerTreeRoot()
    same_name = root.addGroup('FlyPath')

    def add(kind):
        layer = QgsVectorLayer('Point?crs=EPSG:4326', kind, 'memory')
        layer.setCustomProperty('flypath_internal', True)
        module.register(layer, project, kind=kind)
        return layer

    try:
        layers = {kind: add(kind) for kind in (
            'contours', 'takeoff', 'corridor', 'survey', 'path', 'breaks',
            'waypoints')}
        group = root.children()[0]
        assert [node.layer().customProperty('flypath_kind')
                for node in group.children()] == list(module._ORDER)
        group.findLayer(layers['path'].id()).setItemVisibilityChecked(False)
        group.findLayer(layers['waypoints'].id()).setItemVisibilityChecked(False)
        group.setItemVisibilityChecked(False)
        saved = module.visibility(project)
        module.remove([layer.id() for layer in layers.values()], project)
        assert group not in root.children()
        replacements = {kind: add(kind) for kind in reversed(module._ORDER)}
        module.restore_visibility(saved, project)
        group = root.children()[0]
        assert not group.itemVisibilityChecked()
        assert not group.findLayer(replacements['path'].id()).itemVisibilityChecked()
        assert not group.findLayer(replacements['waypoints'].id()).itemVisibilityChecked()
        assert group.findLayer(replacements['takeoff'].id()).itemVisibilityChecked()

        user = QgsVectorLayer('Point?crs=EPSG:4326', 'User data', 'memory')
        project.addMapLayer(user, False)
        user_node = group.addLayer(user)
        user_node.setItemVisibilityChecked(False)
        assert module.visibility(project)[1].get(None) is None
        module.restore_visibility(saved, project)
        assert not user_node.itemVisibilityChecked()
        module.remove([layer.id() for layer in replacements.values()], project)
        assert group in root.children() and group.findLayer(user.id())
        assert same_name in root.children()
        assert project.mapLayer(user.id()) is user
        project.removeMapLayer(user.id())
        module.remove([], project)
        assert group not in root.children()
        assert same_name in root.children()
    finally:
        project.clear()


def test_flight_legend_controls_its_waypoints_and_survives_redraw():
    try:
        from qgis.PyQt.QtCore import Qt
        from qgis.core import QgsApplication, QgsLayerTreeModel, QgsProject
        module = importlib.import_module(
            Path(__file__).resolve().parents[1].name + '.preview_layers')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    project = QgsProject.instance()
    project.clear()
    model = QgsLayerTreeModel(project.layerTreeRoot())
    model.setFlag(getattr(QgsLayerTreeModel, 'Flag', QgsLayerTreeModel)
                  .AllowLegendChangeState, True)
    unchecked = getattr(Qt, 'CheckState', Qt).Unchecked
    checked = getattr(Qt, 'CheckState', Qt).Checked
    check_role = getattr(Qt, 'ItemDataRole', Qt).CheckStateRole
    missions = [[(13, 51), (14, 52)], [(15, 53), (16, 54)]]
    ids = module.create(missions, project=project)

    def layers():
        return [project.mapLayer(layer_id) for layer_id in ids]

    def legend():
        return model.layerLegendNodes(project.layerTreeRoot().findLayer(ids[0]))

    try:
        path, points = layers()
        assert points.featureCount() == 4
        assert model.setData(model.legendNode2index(legend()[0]),
                             unchecked, check_role)
        assert [feature['mission'] for feature in points.getFeatures()] == [1, 1]
        assert points.labelsEnabled()
        assert len(points.renderer().rootRule().children()) == 3

        waypoint_node = project.layerTreeRoot().findLayer(ids[1])
        waypoint_node.setItemVisibilityChecked(False)
        assert path.renderer().rootRule().children()[1].active()
        assert not waypoint_node.itemVisibilityChecked()
        waypoint_node.setItemVisibilityChecked(True)
        assert points.featureCount() == 2

        changed = [[(20, 60), (21, 61), (22, 62)],
                   [(30, 70), (31, 71), (32, 72)]]
        assert module.redraw(ids, changed, project=project) == ids
        assert [node.data(check_role) for node in legend()] == [
            unchecked, checked]
        assert points.featureCount() == 3
        assert all(feature.geometry().asPoint().x() >= 30
                   for feature in points.getFeatures())
        assert model.setData(model.legendNode2index(legend()[0]),
                             checked, check_role)
        assert points.featureCount() == 6
        assert any(feature.geometry().asPoint().x() == 22
                   for feature in points.getFeatures())

        assert model.setData(model.legendNode2index(legend()[1]),
                             unchecked, check_role)
        project.removeMapLayer(ids[0])
        ids = module.redraw(ids, changed + [[(40, 80), (41, 81)]],
                            project=project)
        path, points = layers()
        assert [node.data(check_role) for node in legend()] == [
            checked, unchecked, checked]
        assert {feature['mission'] for feature in points.getFeatures()} == {0, 2}
        project.layerTreeRoot().findLayer(ids[1]).setItemVisibilityChecked(False)
        assert path.renderer().rootRule().children()[0].active()
        assert not project.layerTreeRoot().findLayer(ids[1]).itemVisibilityChecked()
        project.layerTreeRoot().children()[0].setItemVisibilityChecked(False)
        assert not project.layerTreeRoot().children()[0].itemVisibilityChecked()
        project.removeMapLayer(ids[1])
        assert model.setData(model.legendNode2index(legend()[0]),
                             unchecked, check_role)
        ids = module.redraw(ids, changed, project=project)
        assert [node.data(check_role) for node in legend()] == [
            unchecked, unchecked]
        assert project.mapLayer(ids[1]).featureCount() == 0
        module.remove(ids, project)
        app.processEvents()
    finally:
        project.clear()


def test_shared_flight_endpoints_render_with_their_own_numbers():
    try:
        from qgis.PyQt.QtCore import QSize
        from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,
                               QgsMapRendererSequentialJob, QgsMapSettings,
                               QgsProject, QgsRectangle)
        module = importlib.import_module(
            Path(__file__).resolve().parents[1].name + '.preview_layers')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    project = QgsProject.instance()
    project.clear()
    missions = [[(13.000, 51), (13.004, 51), (13.008, 51),
                 (13.012, 51), (13.016, 51)],
                [(13.016, 51), (13.020, 51), (13.024, 51)]]
    ids = module.create(missions, project=project)
    path, points = [project.mapLayer(layer_id) for layer_id in ids]

    def render_labels():
        settings = QgsMapSettings()
        settings.setLayers([points, path])
        settings.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
        settings.setExtent(QgsRectangle(12.997, 50.992, 13.027, 51.008))
        settings.setOutputSize(QSize(1200, 640))
        settings.setOutputDpi(96)
        job = QgsMapRendererSequentialJob(settings)
        job.start()
        job.waitForFinished()
        return {label.featureId: label for label in job.takeLabelingResults().allLabels()
                if label.layerID == points.id()}

    try:
        shared = {feature['wp_type']: feature for feature in points.getFeatures()
                  if feature.geometry().asPoint().x() == 13.016}
        assert set(shared) == {'end', 'start'}
        assert shared['end'].geometry().asPoint() == shared['start'].geometry().asPoint()
        labels = render_labels()
        end = labels[shared['end'].id()]
        start = labels[shared['start'].id()]
        assert (end.labelText, start.labelText) == ('5', '1')
        end_x = end.labelRect.center().x()
        start_x = start.labelRect.center().x()
        assert end_x < 13.016 < start_x
        assert start_x - end_x > end.labelRect.width() + start.labelRect.width()

        path.renderer().rootRule().children()[0].setActive(False)
        path.triggerRepaint()
        app.processEvents()
        assert [feature['mission'] for feature in points.getFeatures()] == [1, 1, 1]
        labels = render_labels()
        assert shared['end'].id() not in labels
        assert labels[shared['start'].id()].labelText == '1'
    finally:
        project.clear()


def test_empty_flights_do_not_break_endpoint_offsets():
    try:
        from qgis.core import QgsApplication
        module = importlib.import_module(
            Path(__file__).resolve().parents[1].name + '.preview_layers')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    layer = module._waypoint_layer([[(13, 51)], [], [(14, 52)]])
    assert [feature['display_offset'] for feature in layer.getFeatures()] == [
        '0,0', '0,0']


if __name__ == '__main__':
    test_preview_layers_create_redraw_and_recover()
    print('preview layer tests passed')
