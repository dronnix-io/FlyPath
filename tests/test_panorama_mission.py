"""Panorama missions: place a point, preview the laps, export, then leave (no network)."""

import importlib
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_panorama_point_previews_exports_and_resets():
    try:
        from qgis.core import QgsApplication
        from qgis.gui import QgsMapCanvas, QgsMapToolPan
        from qgis.PyQt.QtCore import QSettings
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    package = Path(__file__).resolve().parents[1].name
    dialog = importlib.import_module(package + '.flypath_dialog')
    mission_export = importlib.import_module(package + '.mission_export')
    sync = importlib.import_module(package + '.flypath_sync')
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    ini = os.path.join(tempfile.mkdtemp(), 'settings.ini')
    ini_format = getattr(QSettings, 'Format', QSettings).IniFormat
    canvas = QgsMapCanvas()
    pan = QgsMapToolPan(canvas)
    canvas.setMapTool(pan)

    def shown(widget):
        return not widget.isHidden()

    with patch.object(dialog, 'QSettings', lambda *args: QSettings(ini, ini_format)):
        planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            planner.show()
            planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
            planner.missionTypeCombo.setCurrentText('Panorama')
            app.processEvents()
            assert planner._mission_kind() == 'panorama' and planner._mission_type() == 'full'
            assert all(shown(widget) for widget in (
                planner.panoramaOverlapSpin, planner.panoramaNadirCheck, planner.placePointBtn))
            still_shown = [name for name in (
                '_captureRow', '_sourceStack', 'sideOverlapSpin', 'frontOverlapStack', 'gsdSpin',
                'splitCheck', 'maxWaypointsSpin', 'terrainFollowCheck', 'reverseRouteCheck',
                'orbitRadiusSpin', 'demCombo', '_takeoffGroup') if shown(getattr(planner, name))]
            assert not still_shown, still_shown

            # A click places the point and previews it; Escape ends placing.
            planner.placePointBtn.setChecked(True)
            assert canvas.mapTool() is planner._panorama_tool
            planner._on_panorama_point_placed((-114.09, 51.05))
            result = planner._planning.result
            assert result['operation'] == 'plan_panorama'
            assert [(row['gimbal_pitch_deg'], row['photo_count']) for row in result['panorama']['rows']] == [
                (0.0, 8), (-30.0, 8), (-60.0, 7), (-90.0, 1)]
            assert planner.photosLabel.text() == '24' and planner.linesLabel.text() == '4 rows'
            assert planner.frontOverlapStatLabel.text() == '30%'
            planner._on_panorama_point_placed(None)
            assert planner._panorama_tool is None and canvas.mapTool() is pan
            assert not planner.placePointBtn.isChecked()

            # Export: outward headings, a camera tilt per row, hover and photo everywhere.
            flight = dialog.planning_adapter.consume_result(
                planner._planning.request, planner._planning.result)[0]
            path = os.path.join(tempfile.mkdtemp(), 'pano.kmz')
            mission_export.write_kmz(path, planner._export_settings(flight), flight['waypoints'],
                                     'Panorama', actions=flight['actions'])
            wpml = zipfile.ZipFile(path).read('wpmz/waylines.wpml').decode()
            assert wpml.count('<Placemark>') == 24
            assert wpml.count('takePhoto') == wpml.count('<wpml:actionActuatorFunc>hover') == 24
            assert re.findall(r'<wpml:gimbalPitchRotateAngle>([-\d.]+)<', wpml) == [
                '0.0', '-30.0', '-60.0', '-90.0']
            assert re.findall(r'<wpml:waypointHeadingAngle>([-\d.]+)<', wpml)[:3] == ['0.0', '45.0', '90.0']

            # flypath.io does not store panoramas yet.
            try:
                planner._website_payload('Panorama')
            except sync.FlypathSyncError as exc:
                assert 'Panorama' in str(exc)
            else:
                raise AssertionError('A panorama was sent to flypath.io')

            # Leaving restores the mapping rows and drops the point.
            planner.missionTypeCombo.setCurrentText('2D Mapping')
            app.processEvents()
            assert planner._panorama_point is None and planner._panorama_layer_id is None
            assert all(shown(widget) for widget in (
                planner._captureRow, planner.maxWaypointsSpin, planner._sourceStack,
                planner.sideOverlapSpin))
            assert not shown(planner.panoramaOverlapSpin)

            # Remove clears the point and its waypoints.
            planner.missionTypeCombo.setCurrentText('Panorama')
            planner.set_panorama_point(-114.09, 51.05)
            planner._on_preview()
            assert planner._preview_layer_ids
            planner.removePointBtn.click()
            assert planner._panorama_point is None and not planner._preview_layer_ids
        finally:
            planner.close()
            app.processEvents()
    canvas.unsetMapTool(pan)
    canvas.close()
    canvas.deleteLater()
    app.processEvents()
