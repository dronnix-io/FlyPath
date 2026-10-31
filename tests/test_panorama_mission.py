"""Panorama missions: place points, preview the laps, export, then leave (no network)."""

import importlib
import math
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
        from qgis.PyQt.QtCore import QSettings, Qt
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
                '_captureToggle', '_pathCaption', '_sourceStack', 'sideOverlapSpin', 'frontOverlapStack', 'gsdSpin',
                'splitCheck', 'terrainFollowCheck', 'reverseRouteCheck',
                'orbitRadiusSpin', 'demCombo', '_takeoffGroup') if shown(getattr(planner, name))]
            assert not still_shown, still_shown
            # The capture row offers the Flight Path alone.
            assert shown(planner._captureRow) and shown(planner._pathRow)
            assert dialog.form_rows.label(planner._mission_form, planner._captureRow).text() == 'Flight Path'

            # A click places the point and previews it; Escape ends placing.
            planner.placePointBtn.setChecked(True)
            assert canvas.mapTool() is planner._panorama_tool
            planner._on_panorama_point_placed((-114.09, 51.05))
            result = planner._planning.result
            assert result['operation'] == 'plan_panorama'
            assert [(row['gimbal_pitch_deg'], row['photo_count']) for row in result['panorama']['points'][0]['rows']] == [
                (0.0, 8), (-30.0, 8), (-60.0, 7), (-90.0, 1)]
            assert planner.photosLabel.text() == '24' and planner.linesLabel.text() == '4 rows'
            assert planner.frontOverlapStatLabel.text() == '30%'
            planner._panorama_tool.keyPressEvent(SimpleNamespace(key=lambda: Qt.Key.Key_Escape))
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
            assert wpml.count('<wpml:useStraightLine>0<') == 24

            # Straight: the same waypoints with straight legs, stopping at each one.
            planner.pathStraightRadio.click()
            planner._on_preview()
            assert planner._planning.result['turn_style'] == 'straight'
            flight = dialog.planning_adapter.consume_result(
                planner._planning.request, planner._planning.result)[0]
            mission_export.write_kmz(path, planner._export_settings(flight), flight['waypoints'],
                                     'Panorama', actions=flight['actions'])
            wpml = zipfile.ZipFile(path).read('wpmz/waylines.wpml').decode()
            assert wpml.count('<wpml:useStraightLine>1<') == 24
            assert wpml.count('toPointAndStopWithDiscontinuityCurvature') == 24
            planner.pathCurvedRadio.click()

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
            assert not planner._panorama_points and planner._panorama_layer_id is None
            assert all(shown(widget) for widget in (
                planner._captureRow, planner._captureToggle, planner._pathCaption,
                planner.maxWaypointsSpin, planner._sourceStack, planner.sideOverlapSpin))
            assert dialog.form_rows.label(planner._mission_form, planner._captureRow).text() == 'Capture'
            assert not shown(planner.panoramaOverlapSpin)

            # Remove clears the point and its waypoints.
            planner.missionTypeCombo.setCurrentText('Panorama')
            planner.set_panorama_point(-114.09, 51.05)
            planner._on_preview()
            assert planner._preview_layer_ids
            planner.removePointBtn.click()
            assert not planner._panorama_points and not planner._preview_layer_ids
        finally:
            planner.close()
            app.processEvents()
    canvas.unsetMapTool(pan)
    canvas.close()
    canvas.deleteLater()
    app.processEvents()


def test_multiple_panorama_points_number_split_and_undo():
    try:
        from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsPointXY, QgsProject,
                               QgsRectangle)
        from qgis.gui import QgsMapCanvas, QgsMapMouseEvent
        from qgis.PyQt.QtCore import QEvent, QPoint, QSettings, Qt
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    package = Path(__file__).resolve().parents[1].name
    dialog = importlib.import_module(package + '.flypath_dialog')
    controller = importlib.import_module(package + '.panorama_controller')
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    ini = os.path.join(tempfile.mkdtemp(), 'settings.ini')
    ini_format = getattr(QSettings, 'Format', QSettings).IniFormat
    canvas = QgsMapCanvas()

    def east(metres):
        """A point this far east of the first one (close enough at this scale)."""
        return (-114.09 + metres / (111_320 * math.cos(math.radians(51.05))), 51.05)

    with patch.object(dialog, 'QSettings', lambda *args: QSettings(ini, ini_format)):
        planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            planner.show()
            planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
            mapping_limit = planner.maxWaypointsSpin.value()
            planner.missionTypeCombo.setCurrentText('Panorama')
            assert planner.panoramaPointsRadio.isChecked()
            assert planner.placePointBtn.text() == 'Place Points on Map'
            assert planner.maxWaypointsSpin.value() == 200 and not planner.maxWaypointsSpin.isHidden()
            planner.placePointBtn.setChecked(True)
            for metres in (0, 40, 80):
                planner._on_panorama_point_placed(east(metres))
            assert len(planner._panorama_points) == 3 and planner.panoramaPointLabel.text() == '3 points'
            assert planner.linesLabel.text() == '3 points' and planner.photosLabel.text() == '72'
            labels = [feature['label'] for feature in
                      QgsProject.instance().mapLayer(planner._panorama_layer_id).getFeatures()]
            assert labels == ['1', '2', '3']
            assert not planner.undoPointBtn.isHidden()

            # A point too close to another is refused and nothing changes.
            with patch.object(controller.QMessageBox, 'information') as refused:
                planner._on_panorama_point_placed(east(85))
            refused.assert_called_once()
            assert 'closer than' in refused.call_args[0][2]
            assert len(planner._panorama_points) == 3

            # A lower waypoint limit splits between panoramas; the RC picker names them.
            planner.maxWaypointsSpin.setValue(50)
            app.processEvents()
            planner._on_preview()
            assert [planner.splitPartCombo.itemText(i) for i in range(planner.splitPartCombo.count())] == [
                'Part 1 of 2 (points 1 to 2)', 'Part 2 of 2 (point 3)']

            # Undo removes the last point.
            planner.undoPointBtn.click()
            assert len(planner._panorama_points) == 2 and planner.panoramaPointLabel.text() == '2 points'

            # With the mouse: a drag from a point moves it, a click on a point
            # leaves it, and a click on empty map adds one.
            canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
            canvas.resize(800, 600)
            canvas.setExtent(QgsRectangle(-114.0915, 51.0485, -114.0870, 51.0515))
            app.processEvents()
            tool = planner._panorama_tool

            def mouse(kind, pos):
                event_type = {'press': QEvent.Type.MouseButtonPress, 'move': QEvent.Type.MouseMove,
                              'release': QEvent.Type.MouseButtonRelease}[kind]
                button = Qt.MouseButton.NoButton if kind == 'move' else Qt.MouseButton.LeftButton
                event = QgsMapMouseEvent(canvas, event_type, pos, button)
                {'press': tool.canvasPressEvent, 'move': tool.canvasMoveEvent,
                 'release': tool.canvasReleaseEvent}[kind](event)

            second = tool.toCanvasCoordinates(QgsPointXY(*planner._panorama_points[1]))
            before = list(planner._panorama_points)
            mouse('press', second)
            mouse('release', second)
            assert planner._panorama_points == before, 'a click on a point leaves it'
            target = second + QPoint(0, -60)
            mouse('press', second)
            mouse('move', target)
            mouse('release', target)
            assert len(planner._panorama_points) == 2 and planner._panorama_points[0] == before[0]
            assert planner._panorama_points[1][1] > before[1][1], 'dragged north'
            assert planner._planning.result['panorama']['points'][1]['centre']['latitude_deg'] > before[1][1]
            empty = second + QPoint(-200, 150)
            mouse('press', empty)
            mouse('release', empty)
            assert len(planner._panorama_points) == 3
            # A drag that ends too close to another point is refused.
            with patch.object(controller.QMessageBox, 'information') as refused:
                start = tool.toCanvasCoordinates(QgsPointXY(*planner._panorama_points[2]))
                end = tool.toCanvasCoordinates(QgsPointXY(*planner._panorama_points[0])) + QPoint(3, 0)
                mouse('press', start)
                mouse('move', end)
                mouse('release', end)
            assert refused.call_args[0][1] == 'Point Not Moved' and len(planner._panorama_points) == 3

            # Leaving restores the mapping waypoint limit.
            planner.missionTypeCombo.setCurrentText('2D Mapping')
            assert planner.maxWaypointsSpin.value() == mapping_limit
        finally:
            planner.close()
            app.processEvents()
    canvas.close()
    canvas.deleteLater()
    app.processEvents()


def test_panorama_area_places_points_on_a_hexagon_grid():
    try:
        from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsProject
        from qgis.gui import QgsMapCanvas
        from qgis.PyQt.QtCore import QSettings
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    package = Path(__file__).resolve().parents[1].name
    dialog = importlib.import_module(package + '.flypath_dialog')
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    ini = os.path.join(tempfile.mkdtemp(), 'settings.ini')
    ini_format = getattr(QSettings, 'Format', QSettings).IniFormat
    canvas = QgsMapCanvas()
    area = QgsGeometry.fromWkt('POLYGON((-114.09 51.05, -114.08 51.05, -114.08 51.055, '
                               '-114.09 51.055, -114.09 51.05))')

    def shown(widget):
        return not widget.isHidden()

    def markers():
        layer = QgsProject.instance().mapLayer(planner._panorama_layer_id)
        return [feature['label'] for feature in layer.getFeatures()]

    with patch.object(dialog, 'QSettings', lambda *args: QSettings(ini, ini_format)):
        planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            planner.show()
            planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
            planner.missionTypeCombo.setCurrentText('Panorama')
            planner.altitudeSpin.setValue(180)
            planner.speedSpin.setValue(8)
            planner.panoramaAreaRadio.click()
            # The usual area sources replace the point buttons.
            assert shown(planner._sourceStack) and shown(planner.areaLabel)
            assert shown(planner.panoramaSpacingSpin) and not shown(planner._panoramaPlaceRow)
            assert planner.panoramaSpacingSpin.text() == 'Auto (180 m)'

            # Choosing an area previews its points at once, numbered in flight order.
            planner._set_survey_polygon(area, QgsCoordinateReferenceSystem('EPSG:4326'))
            result = planner._planning.result
            assert result['panorama']['layout'] == 'area' and result['panorama']['point_spacing_m'] == 180
            assert planner.panoramaPointLabel.text() == '11 points · 180 m apart'
            assert planner.linesLabel.text() == '11 points' and planner.coverageLabel.text() == '39.0 ha'
            assert markers() == [str(number) for number in range(1, 12)]
            assert planner._preview_layer_ids

            # A set spacing places fewer, wider points.
            planner.panoramaSpacingSpin.setValue(300)
            app.processEvents()
            if planner._planning.settings_changed():
                planner._on_preview()
            assert len(planner._planning.result['panorama']['points']) == 7 and len(markers()) == 7
            # Auto follows the altitude.
            planner.panoramaSpacingSpin.setValue(0)
            planner.altitudeSpin.setValue(200)
            assert planner.panoramaSpacingSpin.text() == 'Auto (200 m)'

            # Back to points clears the area; leaving Panorama restores the 2D rows.
            planner.panoramaPointsRadio.click()
            assert planner._survey_polygon is None and planner._panorama_layer_id is None
            assert shown(planner._panoramaPlaceRow) and not shown(planner._sourceStack)
            assert not shown(planner.panoramaSpacingSpin)
            planner.missionTypeCombo.setCurrentText('2D Mapping')
            assert shown(planner.areaLabel) and shown(planner._sourceStack)
        finally:
            planner.close()
            app.processEvents()
    canvas.close()
    canvas.deleteLater()
    app.processEvents()
