"""Oblique Mapping through the installed QGIS planner (no network)."""

import importlib
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

AREA_WKT = ('POLYGON((-114.09 51.05, -114.087 51.05, -114.087 51.0515, '
            '-114.09 51.0515, -114.09 51.05))')


class _KeepUserSettings:
    """Restore the user's FlyPath settings (export folder, destination) after a test."""

    def __enter__(self):
        from qgis.PyQt.QtCore import QSettings
        self.settings = QSettings('FlyPath', 'FlyPath')
        self.saved = {key: self.settings.value(key) for key in self.settings.allKeys()}

    def __exit__(self, *exc):
        self.settings.clear()
        for key, value in self.saved.items():
            self.settings.setValue(key, value)
        self.settings.sync()


def test_oblique_mapping_plans_labels_and_exports_each_pass():
    try:
        importlib.import_module('qgis.PyQt.QtCore')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    with _KeepUserSettings():
        _test_oblique_mapping()


def _values(wpml, tag):
    return [text.split('<')[0] for text in wpml.split('<wpml:%s>' % tag)[1:]]


def _test_oblique_mapping():
    try:
        from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry
        from qgis.gui import QgsMapCanvas
        package = Path(__file__).resolve().parents[1].name
        dialog = importlib.import_module(package + '.flypath_dialog')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    canvas = QgsMapCanvas()
    planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
    try:
        planner.show()
        app.processEvents()
        planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
        planner.missionTypeCombo.setCurrentText('Oblique Mapping')
        assert planner._mission_kind() == 'oblique'
        # The 2D grid controls plus the pattern, camera angle and nadir pass;
        # reverse route and terrain follow apply, cross-hatch does not.
        for widget in (planner._obliquePatternRow, planner.obliquePitchSpin,
                       planner.obliqueNadirCheck, planner._dirRow, planner.marginSpin,
                       planner.reverseRouteCheck, planner.terrainFollowCheck):
            assert widget.isVisibleTo(planner), widget
        for widget in (planner.crossHatchCheck, planner.bufferSpin):
            assert not widget.isVisibleTo(planner), widget
        assert planner.obliquePitchSpin.value() == -45
        assert planner.obliqueFastRadio.isChecked()

        planner.obliqueFullRadio.setChecked(True)
        planner.captureFullRadio.setChecked(True)
        planner.altitudeSpin.setValue(60)
        planner.obliquePitchSpin.setValue(-55)
        planner._set_survey_polygon(QgsGeometry.fromWkt(AREA_WKT),
                                    QgsCoordinateReferenceSystem('EPSG:4326'))
        planner._on_preview()
        request, result = planner._planning.request, planner._planning.result
        assert request['operation'] == 'plan_oblique'
        assert request['gimbal_pitch_deg'] == -55 and request['include_nadir'] is True
        assert request['pattern'] == 'full'
        assert result['validation']['export_allowed'], result['validation']
        passes = result['oblique']['passes']
        assert [p['kind'] for p in passes] == ['nadir', 'forward', 'right', 'back', 'left']
        flights = result['flights']
        assert len(planner._missions) == len(flights) == int(planner.batteriesLabel.text())
        labels = [planner.splitPartCombo.itemText(i) for i in range(planner.splitPartCombo.count())]
        rows = result['route']['waypoints']
        kinds = [passes[rows[flight['route_start_waypoint_index']]['pass_index']]['kind'] for flight in flights]
        assert len(labels) == len(flights)
        for label, kind in zip(labels, kinds):
            assert label.endswith('(Nadir)') if kind == 'nadir' else '(Facing ' in label, label
        facing = [label.split('(Facing ')[1] for label in labels if '(Facing ' in label]
        assert sorted(set(facing)) == ['E)', 'N)', 'S)', 'W)']

        # Each flight is its own KMZ: the nadir pass faces along its lines,
        # the oblique passes hold their heading with the camera at -55.
        folder = tempfile.mkdtemp()
        planner.destCombo.setCurrentIndex(planner.destCombo.findData('local'))
        planner.localFolderEdit.setText(folder)
        path = str(Path(folder) / 'oblique.kmz')
        with patch.object(dialog.QFileDialog, 'getSaveFileName', return_value=(path, '')), \
                patch.object(dialog.QMessageBox, 'information', return_value=dialog._MB_NO), \
                patch.object(dialog.QMessageBox, 'warning') as warning, \
                patch.object(dialog.QMessageBox, 'critical') as critical:
            planner._on_export()
        assert not warning.called and not critical.called
        files = sorted(Path(folder).glob('*.kmz'))
        assert len(files) == len(flights)
        rows = result['route']['waypoints']
        for kmz, flight in zip(files, flights):
            with zipfile.ZipFile(kmz) as archive:
                wpml = archive.read('wpmz/waylines.wpml').decode('utf-8')
            survey_pass = passes[rows[flight['route_start_waypoint_index']]['pass_index']]
            modes = set(_values(wpml, 'waypointHeadingMode'))
            pitches = {float(value) for value in _values(wpml, 'waypointGimbalPitchAngle')}
            rotations = [float(value) for value in _values(wpml, 'gimbalPitchRotateAngle')]
            assert len(_values(wpml, 'executeHeight')) == flight['waypoint_count']
            assert rotations[0] == survey_pass['gimbal_pitch_deg']
            if survey_pass['kind'] == 'nadir':
                assert modes == {'followWayline'} and pitches == {-90.0}
            else:
                headings = {float(value) for value in _values(wpml, 'waypointHeadingAngle')}
                assert modes == {'smoothTransition'} and pitches == {-55.0}
                assert headings == {round(survey_pass['heading_deg'], 1)}

        mission = planner._website_payload('Oblique')
        assert mission['settings']['oblique_pattern'] == 'full'
        assert mission['settings']['oblique_pitch'] == -55
        assert mission['settings']['oblique_nadir'] is True
        saved = deepcopy(mission['planning_result'])
        with patch.object(planner, '_plan_shared', side_effect=AssertionError('regenerated')):
            adjusted, note = planner._apply_website_mission(mission)
        assert not adjusted and not note
        assert planner._mission_kind() == 'oblique'
        assert planner.obliqueFullRadio.isChecked()
        assert planner.obliquePitchSpin.value() == -55
        assert planner._planning.result == saved
        assert planner._website_payload('Oblique again')['planning_result'] == saved

        # Fast flies the nadir pass and two crossing grids facing along their
        # lines, exported without a held heading.
        planner.obliqueFastRadio.setChecked(True)
        assert planner._planning.save_requires_regeneration()
        planner._on_preview()
        result = planner._planning.result
        passes = result['oblique']['passes']
        assert [p['kind'] for p in passes] == ['nadir', 'grid', 'cross_grid']
        labels = [planner.splitPartCombo.itemText(i) for i in range(planner.splitPartCombo.count())]
        assert any(label.endswith('(Grid)') for label in labels)
        assert any(label.endswith('(Cross grid)') for label in labels)
        folder = tempfile.mkdtemp()
        planner.localFolderEdit.setText(folder)
        path = str(Path(folder) / 'fast.kmz')
        with patch.object(dialog.QFileDialog, 'getSaveFileName', return_value=(path, '')),                 patch.object(dialog.QMessageBox, 'information', return_value=dialog._MB_NO),                 patch.object(dialog.QMessageBox, 'warning') as warning,                 patch.object(dialog.QMessageBox, 'critical') as critical:
            planner._on_export()
        assert not warning.called and not critical.called
        files = sorted(Path(folder).glob('*.kmz'))
        assert len(files) == len(result['flights'])
        rows = result['route']['waypoints']
        for kmz, flight in zip(files, result['flights']):
            with zipfile.ZipFile(kmz) as archive:
                wpml = archive.read('wpmz/waylines.wpml').decode('utf-8')
            survey_pass = passes[rows[flight['route_start_waypoint_index']]['pass_index']]
            assert set(_values(wpml, 'waypointHeadingMode')) == {'followWayline'}
            pitches = {float(value) for value in _values(wpml, 'waypointGimbalPitchAngle')}
            assert pitches == {float(survey_pass['gimbal_pitch_deg'])}
        assert {p['gimbal_pitch_deg'] for p in passes[1:]} == {-55}

        # Reverse route flies each pass from its other end.
        forward_rows = planner._planning.result['route']['waypoints']
        planner.reverseRouteCheck.setChecked(True)
        assert planner._planning.request['reverse_route'] is True
        reverse = planner._planning.result
        assert reverse['oblique']['reverse_route'] is True
        for ahead, back in zip(result['oblique']['passes'], reverse['oblique']['passes']):
            assert (reverse['route']['waypoints'][back['start_waypoint_index']]['position']
                    == forward_rows[ahead['end_waypoint_index']]['position'])
        planner.reverseRouteCheck.setChecked(False)

        # Changing the nadir pass or the camera angle re-plans at once.
        planner.obliqueNadirCheck.setChecked(False)
        assert [p['kind'] for p in planner._planning.result['oblique']['passes']] == [
            'grid', 'cross_grid']
        planner.obliquePitchSpin.setValue(-40)
        assert planner._planning.result['oblique']['gimbal_pitch_deg'] == -40

        legacy = planner._website_payload('Saved Fast oblique')
        legacy['planning_result'] = dialog.planning_adapter.plan(
            legacy['planning_request'], engine_version='1.4.0')
        legacy['waypoints'] = [[row['position']['latitude_deg'], row['position']['longitude_deg']]
                               for row in legacy['planning_result']['route']['waypoints']]
        with patch.object(planner, '_plan_shared', side_effect=AssertionError('regenerated')):
            planner._apply_website_mission(legacy)
        assert planner._planning.result == legacy['planning_result']
        assert planner._planning.export_issue() == 'regeneration_required'
        assert planner.previewBtn.text() == 'Regenerate on Map'
        with patch.object(planner, '_export_local') as export, patch.object(dialog.QMessageBox, 'warning'):
            planner._on_export()
        export.assert_not_called()
        planner._on_preview()
        assert planner._planning.result['engine_version'] == '1.4.1'
        assert not planner._planning.save_requires_regeneration()
        assert planner._website_payload('Updated Fast oblique')['planning_result']['engine_version'] == '1.4.1'

        # Back in 2D Mapping the oblique rows hide and terrain follow returns.
        planner.missionTypeCombo.setCurrentText('2D Mapping')
        assert not planner.obliquePitchSpin.isVisibleTo(planner)
        assert not planner.obliqueNadirCheck.isVisibleTo(planner)
        assert planner.terrainFollowCheck.isVisibleTo(planner)
    finally:
        planner.close()
        canvas.close()
        app.processEvents()


class _Flat:
    """Level ground at 1,000 m."""

    def sample(self, lon, lat):
        return 1000.0

    def clear(self):
        pass


class _Slope:
    """Ground that rises 20 m for every 0.001 degrees of latitude northwards."""

    def sample(self, lon, lat):
        return 1000.0 + (lat - 51.05) * 20000.0

    def clear(self):
        pass


def test_oblique_terrain_follow_uses_the_higher_ground():
    try:
        importlib.import_module('qgis.PyQt.QtCore')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    with _KeepUserSettings():
        _test_oblique_terrain_follow()


def _test_oblique_terrain_follow():
    try:
        from pyproj import Geod
        from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry
        from qgis.gui import QgsMapCanvas
        package = Path(__file__).resolve().parents[1].name
        dialog = importlib.import_module(package + '.flypath_dialog')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    geod = Geod(ellps='WGS84')
    slope = _Slope()
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    canvas = QgsMapCanvas()
    planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
    try:
        planner.show()
        app.processEvents()
        planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
        planner.missionTypeCombo.setCurrentText('Oblique Mapping')
        planner.captureFullRadio.setChecked(True)
        planner.obliqueFullRadio.setChecked(True)
        planner.altitudeSpin.setValue(60)
        planner.sameTakeoffCheck.setChecked(False)
        planner._terrain = slope
        planner._set_survey_polygon(QgsGeometry.fromWkt(AREA_WKT),
                                    QgsCoordinateReferenceSystem('EPSG:4326'))
        planner.reverseRouteCheck.setChecked(True)
        planner.terrainFollowCheck.setChecked(True)
        # Terrain follow is offered for oblique and keeps Reverse route.
        assert planner.terrainFollowCheck.isVisibleTo(planner)
        assert planner.reverseRouteCheck.isChecked() and planner.reverseRouteCheck.isEnabled()
        assert not planner.terrainToleranceSpin.isVisibleTo(planner)
        planner._on_preview()
        result = planner._planning.result
        rows = result['route']['waypoints']
        passes = result['oblique']['passes']
        offset = result['oblique']['offset_m']
        terrain = planner._oblique_terrain
        assert len(terrain) == len(result['flights'])
        for flight, plan in zip(result['flights'], terrain):
            start, end = flight['route_start_waypoint_index'], flight['route_end_waypoint_index']
            # Full automatic keeps the photo waypoints and sets their heights.
            assert len(plan['waypoints']) == len(plan['heights']) == end - start + 1
            base = slope.sample(rows[start]['position']['longitude_deg'], rows[start]['position']['latitude_deg'])
            for row, height in zip(rows[start:end + 1], plan['heights']):
                lon, lat = row['position']['longitude_deg'], row['position']['latitude_deg']
                survey_pass = passes[row['pass_index']]
                ground = slope.sample(lon, lat)
                if survey_pass['kind'] != 'nadir':
                    target = geod.fwd(lon, lat, survey_pass['heading_deg'], offset)
                    ground = max(ground, slope.sample(target[0], target[1]))
                assert height == round(60 + ground - base, 1)
        # The north-facing pass looks up the slope, so it flies higher than the
        # ground below it alone would ask for.
        north = next(index for index, p in enumerate(passes)
                     if p['heading_deg'] is not None and abs(p['heading_deg']) < 45)
        flight = next(index for index, f in enumerate(result['flights'])
                      if rows[f['route_start_waypoint_index']]['pass_index'] == north)
        assert terrain[flight]['heights'][0] > 60 + 20000.0 * offset / 111_200 - 1
        # The exported flight carries those heights.
        settings = planner._export_settings(planning_consumed(planner, flight))
        assert settings.heights == tuple(terrain[flight]['heights'])

        # Semi-automatic works like 2D Mapping: waypoints are added along a leg
        # only where the ground changes by more than the terrain tolerance.
        planner.captureSemiRadio.setChecked(True)
        assert planner.terrainToleranceSpin.isVisibleTo(planner)
        planner._on_preview()
        assert 'densify_m' not in planner._planning.request
        result = planner._planning.result
        terrain = planner._oblique_terrain
        engine_points = sum(f['waypoint_count'] for f in result['flights'])
        added = sum(len(plan['waypoints']) for plan in terrain)
        assert added > engine_points
        assert all(len(plan['waypoints']) <= planner.maxWaypointsSpin.value() for plan in terrain)
        # The map preview and the waypoint count show the added waypoints.
        from qgis.core import QgsProject
        assert planner._missions == [plan['waypoints'] for plan in terrain]
        assert planner.waypointsLabel.text() == f'{added:,}'
        layers = [QgsProject.instance().mapLayer(layer_id) for layer_id in planner._preview_layer_ids]
        assert max(layer.featureCount() for layer in layers if layer is not None) == added
        # A coarser tolerance adds fewer waypoints; flat ground adds none.
        planner.terrainToleranceSpin.setValue(20)
        coarse = sum(len(plan['waypoints']) for plan in planner._oblique_terrain)
        assert engine_points < coarse < added
        planner._terrain = _Flat()
        planner._on_preview()
        assert sum(len(plan['waypoints']) for plan in planner._oblique_terrain) == sum(
            f['waypoint_count'] for f in planner._planning.result['flights'])
        # Each exported flight matches its terrain waypoints, heading and tilt.
        consumed = planning_consumed(planner, len(result['flights']) - 1)
        settings = planner._export_settings(consumed)
        count = len(planner._oblique_terrain[-1]['waypoints'])
        assert len(settings.heights) == count and len(settings.gimbal_pitches) == count

        from FlyPath.flypath_sync import FlypathSyncError
        with unittest.TestCase().assertRaisesRegex(FlypathSyncError, 'terrain follow'):
            planner._website_payload('Terrain oblique')
        with patch.object(dialog, 'densify_by_terrain', side_effect=ValueError('waypoint limit')), \
                patch.object(dialog.QMessageBox, 'warning') as warning:
            planner._on_preview()
            assert planner._terrain_failed and planner._oblique_terrain is None
            planner._on_export()
        assert warning.called

        # Off again, no terrain waypoints.
        planner.terrainFollowCheck.setChecked(False)
        assert planner._oblique_terrain is None
    finally:
        planner.close()
        canvas.close()
        app.processEvents()


def planning_consumed(planner, index):
    package = Path(__file__).resolve().parents[1].name
    adapter = importlib.import_module(package + '.planning_adapter')
    return adapter.consume_result(planner._planning.request, planner._planning.result)[index]
