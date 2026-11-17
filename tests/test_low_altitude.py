"""Low-altitude flight: opt in, accept once, plan, warn, confirm export (no network)."""

import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_low_altitude_needs_acceptance_warns_and_confirms_export():
    try:
        from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry
        from qgis.gui import QgsMapCanvas
        from qgis.PyQt.QtCore import QSettings
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    package = Path(__file__).resolve().parents[1].name
    dialog = importlib.import_module(package + '.flypath_dialog')
    low = importlib.import_module(package + '.low_altitude_controller')
    sync = importlib.import_module(package + '.flypath_sync')
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    ini = os.path.join(tempfile.mkdtemp(), 'settings.ini')
    ini_format = getattr(QSettings, 'Format', QSettings).IniFormat
    canvas = QgsMapCanvas()
    area = QgsGeometry.fromWkt('POLYGON((-114.0900 51.0500, -114.0895 51.0500, -114.0895 51.0503, '
                               '-114.0900 51.0503, -114.0900 51.0500))')

    def shown(widget):
        return not widget.isHidden()

    with patch.object(dialog, 'QSettings', lambda *args: QSettings(ini, ini_format)):
        planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            planner.show()
            planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
            planner.missionTypeCombo.setCurrentText('2D Mapping')
            assert planner.altitudeSpin.minimum() == 30 and not planner.lowAltitudeCheck.isChecked()
            assert not shown(planner.lowAltitudeWarning)

            # Declining the responsibility leaves everything as it was.
            with patch.object(low.QMessageBox, 'question', return_value=low._MB_NO) as asked:
                planner.lowAltitudeCheck.click()
            asked.assert_called_once()
            assert 'responsible' in asked.call_args[0][2]
            assert not planner.lowAltitudeCheck.isChecked() and planner.altitudeSpin.minimum() == 30

            # Accepting lowers the minimum to 5 m, and is remembered.
            with patch.object(low.QMessageBox, 'question', return_value=low._MB_YES):
                planner.lowAltitudeCheck.click()
            assert planner.lowAltitudeCheck.isChecked() and planner.altitudeSpin.minimum() == 5
            assert str(QSettings(ini, ini_format).value(low.ACCEPTED_KEY)).lower() == 'true'
            planner.lowAltitudeCheck.click()
            with patch.object(low.QMessageBox, 'question') as asked_again:
                planner.lowAltitudeCheck.click()
            asked_again.assert_not_called()
            assert planner.lowAltitudeCheck.isChecked()

            # 8 m plans normally, with the warning shown under Altitude.
            planner.captureFullRadio.setChecked(True)
            planner.altitudeSpin.setValue(8)
            app.processEvents()
            assert shown(planner.lowAltitudeWarning)
            assert 'Low altitude (8 m)' in planner.lowAltitudeWarning.text()
            assert 'Semi capture' not in planner.lowAltitudeWarning.text()
            planner.captureSemiRadio.setChecked(True)
            assert 'Semi capture' in planner.lowAltitudeWarning.text()
            planner.captureFullRadio.setChecked(True)
            planner._set_survey_polygon(area, QgsCoordinateReferenceSystem('EPSG:4326'))
            planner._on_preview()
            result = planner._planning.result
            assert result and result['validation']['export_allowed']
            assert planner.gsdSpin.value() < 0.5

            # Export asks again; No stops it before any file is written.
            with patch.object(low.QMessageBox, 'question', return_value=low._MB_NO) as confirm, \
                    patch.object(planner, '_export_local') as export_local, \
                    patch.object(planner, '_export_rc') as export_rc:
                planner._on_export()
            confirm.assert_called_once()
            assert '8 m' in confirm.call_args[0][2]
            export_local.assert_not_called()
            export_rc.assert_not_called()
            with patch.object(low.QMessageBox, 'question', return_value=low._MB_YES), \
                    patch.object(planner, '_export_local') as export_local, \
                    patch.object(planner, '_export_rc') as export_rc:
                planner._on_export()
            assert export_local.called or export_rc.called

            # flypath.io does not take low-altitude missions yet.
            try:
                planner._website_payload('Low plot')
            except sync.FlypathSyncError as exc:
                assert 'low-altitude' in str(exc)
            else:
                raise AssertionError('A low-altitude mission was sent to flypath.io')

            # Turning it off raises the altitude back to 30 m and hides the warning.
            planner.lowAltitudeCheck.click()
            assert planner.altitudeSpin.minimum() == 30 and planner.altitudeSpin.value() == 30
            assert not shown(planner.lowAltitudeWarning)
            with patch.object(low.QMessageBox, 'question') as not_asked, \
                    patch.object(planner, '_export_local'), patch.object(planner, '_export_rc'):
                planner._on_preview()
                planner._on_export()
            not_asked.assert_not_called()

            # Orbit and panorama keep their 10 m minimum, and go to 5 m with the option.
            planner.missionTypeCombo.setCurrentText('Panorama')
            assert planner.altitudeSpin.minimum() == 10
            planner.lowAltitudeCheck.click()
            assert planner.altitudeSpin.minimum() == 5
            planner.altitudeSpin.setValue(7)
            assert shown(planner.lowAltitudeWarning) and 'Semi capture' not in planner.lowAltitudeWarning.text()
            planner.missionTypeCombo.setCurrentText('2D Mapping')
            assert planner.altitudeSpin.minimum() == 5
        finally:
            planner.close()
            app.processEvents()
    canvas.close()
    canvas.deleteLater()
    app.processEvents()
