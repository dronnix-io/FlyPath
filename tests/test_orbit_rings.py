"""Multi-ring orbits through the installed QGIS planner (no network)."""

from copy import deepcopy
import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


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


def test_orbit_rings_plan_export_and_reset():
    try:
        importlib.import_module('qgis.PyQt.QtCore')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    with _KeepUserSettings():
        _test_orbit_rings_plan_export_and_reset()


def _test_orbit_rings_plan_export_and_reset():
    try:
        from qgis.core import QgsApplication, QgsProject
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

    def other_rings():
        return [layer for layer in QgsProject.instance().mapLayers().values()
                if layer.customProperty('flypath_kind') == 'orbit_rings']

    try:
        planner.show()
        app.processEvents()
        planner.missionTypeCombo.setCurrentText('Orbit (3D Model)')
        planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
        planner.captureFullRadio.setChecked(True)
        planner.orbitRadiusSpin.setValue(30)
        planner.altitudeSpin.setValue(25)
        planner.orbitTiltSpin.setValue(-20)
        # Rings are added around a drawn circle.
        assert planner._orbitRingRow.isVisibleTo(planner)
        assert not planner.addRingBtn.isEnabled()
        assert not planner.removeRingBtn.isVisibleTo(planner)
        planner.set_orbit_centre(-114, 51)
        planner._on_preview()
        assert planner.linesLabel.text() == '1 ring'
        single = planner._website_payload('One ring')
        assert 'rings' not in single['planning_request']
        assert planner._export_settings().heights is None

        planner.addRingBtn.click()
        assert planner.orbitRingCombo.count() == 2 and planner.orbitRingCombo.currentIndex() == 1
        assert (planner.orbitRadiusSpin.value(), planner.altitudeSpin.value(),
                planner.orbitTiltSpin.value()) == (30, 40, -35)
        assert planner.removeRingBtn.isVisibleTo(planner)
        assert not planner.orbitRingLimitLabel.isVisibleTo(planner)
        # The selector is in Survey Area; Flight Parameters names the ring.
        assert planner._area_form.labelForField(planner._orbitRingRow) is not None
        assert planner._row_label(planner._flight_form, planner.altitudeSpin).text() == 'Altitude (Ring 2)'
        assert planner._row_label(planner._flight_form, planner.orbitTiltSpin).text() == 'Camera Tilt (Ring 2)'
        planner.orbitRadiusSpin.setValue(40)
        planner.addRingBtn.click()
        assert planner.orbitRingCombo.count() == 3
        assert not planner.addRingBtn.isEnabled()
        assert planner.orbitRingLimitLabel.isVisibleTo(planner)
        assert 'up to 3 rings' in planner.infoBar.text()
        planner._on_preview()
        rings = [(30, 25, -20), (40, 40, -35), (40, 55, -50)]
        assert planner._planning.request['rings'] == [
            {'radius_m': radius, 'altitude_m': altitude, 'gimbal_pitch_deg': tilt}
            for radius, altitude, tilt in rings]
        assert planner.linesLabel.text() == '3 rings'
        assert len(other_rings()) == 1 and other_rings()[0].featureCount() == 2

        # Selecting a ring shows its values and keeps the others' edits.
        planner.orbitRingCombo.setCurrentIndex(0)
        assert (planner.orbitRadiusSpin.value(), planner.altitudeSpin.value(),
                planner.orbitTiltSpin.value()) == rings[0]
        assert planner._orbit_rings_from_ui() == rings
        assert not planner._planning.settings_changed()

        rows = planner._planning.result['route']['waypoints']
        settings = planner._export_settings()
        assert settings.heights == tuple(rings[row['ring_index']][1] for row in rows)
        assert settings.gimbal_pitches == tuple(rings[row['ring_index']][2] for row in rows)
        assert settings.altitude_m == 25 and settings.gimbal_pitch == -20

        multi = planner._website_payload('Three rings')
        assert multi['settings']['orbit_rings'] == planner._planning.request['rings']
        assert multi['settings']['orbit_radius'] == rings[0][0]
        assert multi['settings']['altitude'] == rings[0][1]
        assert multi['settings']['orbit_tilt'] == rings[0][2]
        assert multi['settings']['split_enabled'] is False
        assert multi['settings']['split_count'] == 1

        # A failed import keeps the rings; a successful one starts from one ring.
        before = (planner._orbit_rings_from_ui(), planner._orbit_ring_index,
                  deepcopy(planner._planning.result))
        with patch.object(planner, '_restore_imported_route', side_effect=RuntimeError('failed')):
            try:
                planner._apply_website_mission(deepcopy(single))
            except sync_controller.FlypathSyncError:
                pass
            else:
                raise AssertionError('Failed import accepted')
        assert before == (planner._orbit_rings_from_ui(), planner._orbit_ring_index,
                          planner._planning.result)
        assert planner.orbitRingCombo.count() == 3

        planner.orbitRingCombo.setCurrentIndex(1)
        planner.removeRingBtn.click()
        assert planner._orbit_ring_index == 0
        assert planner._orbit_rings_from_ui() == [rings[0], rings[2]]
        assert planner.addRingBtn.isEnabled()
        assert not planner.orbitRingLimitLabel.isVisibleTo(planner)

        planner._apply_website_mission(deepcopy(single))
        assert planner._orbit_rings_from_ui() == [rings[0]]
        assert planner.orbitRingCombo.count() == 1 and not other_rings()
        assert planner._planning.result == single['planning_result']

        planner.addRingBtn.click()
        planner._on_remove_orbit_circle()
        assert planner._orbit_rings_from_ui() == [rings[0]]
        assert not planner.addRingBtn.isEnabled() and not other_rings()
        assert planner._row_label(planner._flight_form, planner.orbitRadiusSpin).text() == 'Radius'
        planner.set_orbit_centre(-114, 51)
        planner.addRingBtn.click()
        planner.missionTypeCombo.setCurrentText('2D Mapping')
        assert planner._row_label(planner._flight_form, planner.altitudeSpin).text() == 'Altitude'
    finally:
        planner.close()
        canvas.close()
        app.processEvents()


def test_orbits_split_into_flights_and_export_one_kmz_each():
    try:
        importlib.import_module('qgis.PyQt.QtCore')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    with _KeepUserSettings():
        _test_orbits_split_into_flights_and_export_one_kmz_each()


def _test_orbits_split_into_flights_and_export_one_kmz_each():
    try:
        import tempfile
        import zipfile
        from qgis.core import QgsApplication
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
        planner.maxWaypointsSpin.setValue(70)
        planner.missionTypeCombo.setCurrentText('Orbit (3D Model)')
        # Orbits get their own waypoint limit, with splitting on as in mapping.
        assert planner.maxWaypointsSpin.value() == 200
        assert planner.splitCheck.isVisibleTo(planner) and planner.splitCheck.isChecked()
        planner.captureFullRadio.setChecked(True)
        planner.orbitRadiusSpin.setValue(30)
        planner.altitudeSpin.setValue(25)
        planner.orbitTiltSpin.setValue(-20)
        planner.set_orbit_centre(-114, 51)
        planner._on_preview()
        # One ring that fits one flight keeps the request flypath.io verifies.
        assert planner._planning.request['split'] == {
            'enabled': False, 'max_waypoints_per_flight': 200}
        planner.addRingBtn.click()
        planner.addRingBtn.click()
        rings = planner._orbit_rings_from_ui()
        planner.maxWaypointsSpin.setValue(60)
        planner._on_preview()
        result = planner._planning.result
        assert planner._planning.request['split']['enabled'] is True
        assert result['validation']['export_allowed'], result['validation']
        flights = result['flights']
        assert len(flights) > 1 and all(f['waypoint_count'] <= 60 for f in flights)
        assert planner.batteriesLabel.text() == str(len(flights))
        assert len(planner._missions) == len(flights)
        assert planner.splitPartCombo.count() == len(flights)

        # Each flight is its own KMZ, with its own headings, heights and tilts.
        folder = tempfile.mkdtemp()
        planner.destCombo.setCurrentIndex(planner.destCombo.findData('local'))
        planner.localFolderEdit.setText(folder)
        path = str(Path(folder) / 'orbit.kmz')
        with patch.object(dialog.QFileDialog, 'getSaveFileName', return_value=(path, '')), \
                patch.object(dialog.QMessageBox, 'information', return_value=dialog._MB_NO), \
                patch.object(dialog.QMessageBox, 'warning') as warning, \
                patch.object(dialog.QMessageBox, 'critical') as critical:
            planner._on_export()
        assert not warning.called and not critical.called
        files = sorted(Path(folder).glob('*.kmz'))
        assert len(files) == len(flights)
        rows = result['route']['waypoints']
        tilts = [ring['gimbal_pitch_deg'] for ring in result['orbit']['rings']]
        for kmz, flight in zip(files, flights):
            with zipfile.ZipFile(kmz) as archive:
                wpml = archive.read('wpmz/waylines.wpml').decode('utf-8')
            part = rows[flight['route_start_waypoint_index']:flight['route_end_waypoint_index'] + 1]
            heights = [float(text.split('<')[0]) for text in wpml.split('<wpml:executeHeight>')[1:]]
            headings = [float(text.split('<')[0]) for text in wpml.split('<wpml:waypointHeadingAngle>')[1:]]
            assert heights == [row['altitude_m'] for row in part]
            assert len(headings) == len(part)
            assert all(abs(written - row['heading_deg']) <= .05 + 1e-9
                       for written, row in zip(headings, part))
            first_tilt = tilts[part[0]['ring_index']]
            assert '<wpml:gimbalPitchRotateAngle>%s</wpml:gimbalPitchRotateAngle>' % float(first_tilt) in wpml

        saved = planner._website_payload('Split rings')
        assert saved['settings']['split_enabled'] is True
        assert saved['settings']['split_count'] == saved['planning_request']['split']['requested_flights']
        sync_controller.flypath_sync.validate_mission(saved)
        with patch.object(planner, '_plan_shared', side_effect=AssertionError('regenerated')):
            adjusted, note = planner._apply_website_mission(saved)
        assert not adjusted and not note
        assert planner._orbit_rings_from_ui() == rings
        assert planner._orbit_ring_index == 0
        assert planner._planning.result == result
        assert len(planner._missions) == len(flights)
        assert planner._export_settings().heights == tuple(row['altitude_m'] for row in rows)
        assert planner._export_settings().headings == tuple(row['heading_deg'] for row in rows)
        assert planner.splitSpin.value() == saved['planning_request']['split']['requested_flights']
        assert planner._orbit_request_from_ui() == saved['planning_request']
        assert not planner._planning.dirty
        planner.orbitRingCombo.setCurrentIndex(2)
        assert planner._orbit_rings_from_ui() == rings
        assert planner._orbit_request_from_ui() == saved['planning_request']
        assert not planner._planning.dirty

        # Turning splitting off asks for it, with the numbers.
        planner.splitCheck.setChecked(False)
        planner._on_preview()
        errors = planner._planning.result['validation']['errors']
        assert errors[0]['code'] == 'splitting_required_waypoint_limit'
        assert 'more than the limit of 60' in errors[0]['message']

        # Leaving orbits brings back the mapping waypoint limit.
        planner.missionTypeCombo.setCurrentText('2D Mapping')
        assert planner.maxWaypointsSpin.value() == 70
    finally:
        planner.close()
        canvas.close()
        app.processEvents()


def test_export_destination_starts_on_the_controller():
    try:
        from qgis.PyQt.QtCore import QSettings
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    with _KeepUserSettings():
        from qgis.core import QgsApplication
        from qgis.gui import QgsMapCanvas
        package = Path(__file__).resolve().parents[1].name
        dialog = importlib.import_module(package + '.flypath_dialog')
        app = QgsApplication.instance() or QgsApplication([], False)
        app.initQgis()
        QSettings('FlyPath', 'FlyPath').remove('dest_mode')     # first use
        canvas = QgsMapCanvas()
        planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            assert planner.destCombo.currentData() == 'rc'
            planner.destCombo.setCurrentIndex(planner.destCombo.findData('local'))
        finally:
            planner.close()
        # The last choice is remembered.
        again = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            assert again.destCombo.currentData() == 'local'
        finally:
            again.close()
            canvas.close()
            app.processEvents()
