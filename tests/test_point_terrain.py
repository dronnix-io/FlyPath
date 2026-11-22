"""Orbit and panorama terrain follow through the installed QGIS planner (no network)."""

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


class _Slope:
    """Ground that rises 20 m for every 0.001 degrees of latitude northwards."""

    def sample(self, lon, lat):
        return 1000.0 + (lat - 51.05) * 20000.0

    def clear(self):
        pass


def _position(row):
    return row['position']['longitude_deg'], row['position']['latitude_deg']


def _expected(rows, altitudes, base, slope):
    return [round(altitude + slope.sample(*_position(row)) - base, 1)
            for row, altitude in zip(rows, altitudes)]


def test_orbit_and_panorama_follow_the_terrain():
    try:
        importlib.import_module('qgis.PyQt.QtCore')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    with _KeepUserSettings():
        _test_point_terrain()


def _test_point_terrain():
    try:
        from qgis.core import QgsApplication
        from qgis.gui import QgsMapCanvas
        package = Path(__file__).resolve().parents[1].name
        dialog = importlib.import_module(package + '.flypath_dialog')
        mission_export = importlib.import_module(package + '.mission_export')
        sync = importlib.import_module(package + '.flypath_sync')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    slope = _Slope()
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    canvas = QgsMapCanvas()
    planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
    check = unittest.TestCase()

    def shown(widget):
        return not widget.isHidden()

    def consumed(index=0):
        return dialog.planning_adapter.consume_result(
            planner._planning.request, planner._planning.result)[index]

    def safety_beside_takeoff():
        return planner._flight_col.indexOf(planner._safetyGroup) >= 0

    try:
        planner.show()
        app.processEvents()
        planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
        planner.missionTypeCombo.setCurrentText('Orbit (3D Model)')
        # Terrain follow is offered; the DEM and the Takeoff Zone come with it.
        assert shown(planner.terrainFollowCheck)
        assert not shown(planner._takeoffGroup) and not shown(planner.demCombo)
        assert not safety_beside_takeoff()

        planner.captureSemiRadio.setChecked(True)
        planner.orbitRadiusSpin.setValue(40)
        planner.altitudeSpin.setValue(30)
        planner.reverseRouteCheck.setChecked(True)      # counterclockwise
        planner._terrain = slope
        planner.set_orbit_centre(-114.09, 51.05)
        planner.terrainFollowCheck.setChecked(True)
        # The orbit direction stays; semi orbits are not densified.
        assert planner.reverseRouteCheck.isChecked() and planner.reverseRouteCheck.isEnabled()
        assert shown(planner._takeoffGroup) and shown(planner.demCombo)
        assert safety_beside_takeoff()
        assert not shown(planner.takeoffGsdVarLabel)
        assert not shown(planner.terrainToleranceSpin)

        planner._on_preview()
        result = planner._planning.result
        rows = result['route']['waypoints']
        terrain = planner._terrain_flights
        assert len(terrain) == len(result['flights'])
        base = slope.sample(*_position(rows[0]))       # Same takeoff for all splits
        for flight, plan in zip(result['flights'], terrain):
            start, end = flight['route_start_waypoint_index'], flight['route_end_waypoint_index']
            part = rows[start:end + 1]
            # The engine waypoints stay; each holds 30 m above its own ground.
            assert plan['waypoints'] == [_position(row) for row in part]
            assert plan['heights'] == _expected(part, [30] * len(part), base, slope)
        heights = [height for plan in terrain for height in plan['heights']]
        assert max(heights) - min(heights) > 10        # 80 m across a 20 % slope

        # Export carries those heights with the planned headings, plus the
        # launch offset from the Takeoff Zone.
        planner.launchOffsetSpin.setValue(2.0)
        flight = consumed()
        settings = planner._export_settings(flight)
        assert settings.heights == tuple(planner._terrain_flights[0]['heights'])
        assert settings.headings == tuple(flight['headings'])
        assert settings.launch_offset_m == 2.0
        path = os.path.join(tempfile.mkdtemp(), 'orbit.kmz')
        mission_export.write_kmz(path, settings, flight['waypoints'], 'Orbit',
                                 actions=flight['actions'])
        wpml = zipfile.ZipFile(path).read('wpmz/waylines.wpml').decode()
        assert [float(value) for value in re.findall(
            r'<wpml:executeHeight>([-\d.]+)<', wpml)] == [
                round(height - 2.0, 1) for height in settings.heights]
        planner.launchOffsetSpin.setValue(0.0)

        # flypath.io would plan it flat, so it stays local.
        with check.assertRaisesRegex(sync.FlypathSyncError, 'Orbit terrain follow'):
            planner._website_payload('Terrain orbit')

        # A second ring keeps its own altitude above the ground.
        planner.addRingBtn.click()
        planner._on_preview()
        rows = planner._planning.result['route']['waypoints']
        assert len({row['altitude_m'] for row in rows}) == 2
        terrain = planner._terrain_flights
        flights = planner._planning.result['flights']
        base = slope.sample(*_position(rows[0]))
        for flight, plan in zip(flights, terrain):
            part = rows[flight['route_start_waypoint_index']:flight['route_end_waypoint_index'] + 1]
            assert plan['heights'] == _expected(part, [row['altitude_m'] for row in part], base, slope)
        # Each split from its own first waypoint when it has its own takeoff.
        planner.sameTakeoffCheck.setChecked(False)
        planner._on_preview()
        for flight, plan in zip(planner._planning.result['flights'], planner._terrain_flights):
            part = rows[flight['route_start_waypoint_index']:flight['route_end_waypoint_index'] + 1]
            assert plan['heights'][0] == part[0]['altitude_m']
        planner.sameTakeoffCheck.setChecked(True)

        # No elevation data: the flat route previews, export is blocked.
        class _NoData:
            def sample(self, lon, lat):
                raise dialog._TerrainError('Elevation data is currently unavailable.')

            def clear(self):
                pass

        planner._terrain = _NoData()
        with patch.object(dialog.QMessageBox, 'warning') as warning:
            planner._on_preview()
            assert planner._terrain_failed and planner._terrain_flights is None
            planner._on_export()
        assert warning.call_count == 2
        planner._terrain = slope

        # Panorama keeps terrain follow on from the orbit.
        planner.missionTypeCombo.setCurrentText('Panorama')
        assert planner.terrainFollowCheck.isChecked() and shown(planner.terrainFollowCheck)
        assert shown(planner._routeTitle) and shown(planner._takeoffGroup) and shown(planner.demCombo)
        assert not shown(planner.reverseRouteCheck) and not shown(planner.takeoffGsdVarLabel)
        planner.set_panorama_points([(-114.09, 51.05), (-114.09, 51.051)])
        planner._on_preview()
        result = planner._planning.result
        rows = result['route']['waypoints']
        altitude = planner.altitudeSpin.value()
        by_point = {}
        for plan in planner._terrain_flights:
            for (_lon, lat), height in zip(plan['waypoints'], plan['heights']):
                by_point.setdefault(round(lat, 4), set()).add(height)
        # Every photo of a point is at one height; the north point's ground is
        # 20 m higher, so it flies 20 m higher above the takeoff.
        assert len(by_point) == 2 and all(len(values) == 1 for values in by_point.values())
        first, second = by_point[51.05].pop(), by_point[51.051].pop()
        assert first == altitude and round(second - first, 1) == 20.0
        flight = consumed()
        settings = planner._export_settings(flight)
        assert settings.heights == tuple(planner._terrain_flights[0]['heights'])
        assert settings.gimbal_pitches == tuple(flight['gimbal_pitches'])
        with check.assertRaisesRegex(sync.FlypathSyncError, 'Panorama terrain follow'):
            planner._website_payload('Terrain panorama')

        # Off again: no terrain heights, and the takeoff controls go.
        planner.terrainFollowCheck.setChecked(False)
        assert planner._terrain_flights is None
        assert not shown(planner._takeoffGroup) and not shown(planner.demCombo)
        assert not safety_beside_takeoff()
        assert planner._export_settings(consumed()).heights is None
    finally:
        planner.close()
        canvas.close()
        app.processEvents()
