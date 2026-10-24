"""Oblique Mapping through the installed QGIS planner (no network)."""

import importlib
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
        sync_controller = importlib.import_module(package + '.website_sync_controller')
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
        # The 2D grid controls plus the camera angle and nadir pass; no
        # terrain follow, cross-hatch or reverse route.
        for widget in (planner._obliquePatternRow, planner.obliquePitchSpin,
                       planner.obliqueNadirCheck, planner._dirRow, planner.marginSpin):
            assert widget.isVisibleTo(planner), widget
        for widget in (planner.terrainFollowCheck, planner.crossHatchCheck,
                       planner.reverseRouteCheck, planner.bufferSpin):
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

        # flypath.io cannot store oblique missions yet.
        try:
            planner._website_payload('Oblique')
        except sync_controller.FlypathSyncError as exc:
            assert 'Oblique Mapping' in str(exc)
        else:
            raise AssertionError('An oblique mission was sent to flypath.io')

        # Fast flies the nadir pass and two crossing grids facing along their
        # lines, exported without a held heading.
        planner.obliqueFastRadio.setChecked(True)
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

        # Changing the nadir pass or the camera angle re-plans at once.
        planner.obliqueNadirCheck.setChecked(False)
        assert [p['kind'] for p in planner._planning.result['oblique']['passes']] == [
            'grid', 'cross_grid']
        planner.obliquePitchSpin.setValue(-40)
        assert planner._planning.result['oblique']['gimbal_pitch_deg'] == -40

        # Back in 2D Mapping the oblique rows hide and terrain follow returns.
        planner.missionTypeCombo.setCurrentText('2D Mapping')
        assert not planner.obliquePitchSpin.isVisibleTo(planner)
        assert not planner.obliqueNadirCheck.isVisibleTo(planner)
        assert planner.terrainFollowCheck.isVisibleTo(planner)
    finally:
        planner.close()
        canvas.close()
        app.processEvents()
