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


def test_orbit_rings_plan_export_and_reset():
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

        # flypath.io does not store rings yet.
        with patch.object(sync_controller.QMessageBox, 'information') as information:
            assert planner._on_send_to_website() is False
        assert information.call_args.args[1] == 'Coming Soon'

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
