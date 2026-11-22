"""Reverse route for routes planned outside the engine (no network)."""

import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

LINE_WKT = 'LINESTRING(-114.09 51.05, -114.0885 51.051, -114.087 51.052)'
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


class _Slope:
    """Ground that rises 20 m for every 0.001 degrees of latitude northwards."""

    def sample(self, lon, lat):
        return 1000.0 + (lat - 51.05) * 20000.0

    def clear(self):
        pass


def _flat(missions):
    return [point for waypoints, _heights, _ground in missions for point in waypoints]


def _route(missions):
    """The flown route, with each joining waypoint of split flights once."""
    route = []
    for point in _flat(missions):
        if not route or route[-1] != point:
            route.append(point)
    return route


def _modules():
    try:
        from qgis.core import QgsApplication
        from qgis.gui import QgsMapCanvas
        package = Path(__file__).resolve().parents[1].name
        dialog = importlib.import_module(package + '.flypath_dialog')
        sync = importlib.import_module(package + '.flypath_sync')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    return app, QgsMapCanvas(), dialog, sync


def test_corridor_reverse_route():
    try:
        importlib.import_module('qgis.PyQt.QtCore')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    with _KeepUserSettings():
        _test_corridor_reverse_route()


def _test_corridor_reverse_route():
    from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry
    app, canvas, dialog, sync = _modules()
    planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
    try:
        planner.show()
        app.processEvents()
        planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
        planner.missionTypeCombo.setCurrentText('Corridor Mapping')
        # Reverse route is offered for corridors; cross-hatch is not.
        assert planner.reverseRouteCheck.isVisibleTo(planner)
        assert planner.reverseRouteCheck.isEnabled()
        assert not planner.crossHatchCheck.isVisibleTo(planner)
        planner.captureSemiRadio.setChecked(True)
        planner.altitudeSpin.setValue(40)
        planner.bufferSpin.setValue(60)                 # several passes
        planner._set_survey_line(QgsGeometry.fromWkt(LINE_WKT),
                                 QgsCoordinateReferenceSystem('EPSG:4326'))
        forward = planner._corridor_missions_with_heights()
        assert len(forward) == 1 and len(forward[0][0]) > 4

        # The whole corridor is flown from the other end.
        planner.reverseRouteCheck.setChecked(True)
        reverse = planner._corridor_missions_with_heights()
        assert _flat(reverse) == _flat(forward)[::-1]
        flat_wps, _ = planner._generate_waypoints()
        assert flat_wps == planner._generate_corridor_waypoints()[0]
        planner.reverseRouteCheck.setChecked(False)
        assert flat_wps[::-1] == planner._generate_waypoints()[0]

        # Two stretches: the last one first, each backwards.
        planner.splitSpin.setValue(2)
        forward = planner._corridor_missions_with_heights()
        assert len(forward) == 2
        planner.reverseRouteCheck.setChecked(True)
        reverse = planner._corridor_missions_with_heights()
        assert [m[0] for m in reverse] == [m[0][::-1] for m in forward[::-1]]
        planner.splitSpin.setValue(1)

        # Terrain follow keeps Reverse route, and heights are measured from
        # the new first waypoint.
        planner.captureFullRadio.setChecked(True)
        planner.reverseRouteCheck.setChecked(False)
        forward = _route(planner._corridor_missions_with_heights())
        planner.reverseRouteCheck.setChecked(True)
        slope = _Slope()
        planner._terrain = slope
        planner.terrainFollowCheck.setChecked(True)
        assert planner.reverseRouteCheck.isChecked() and planner.reverseRouteCheck.isEnabled()
        missions = planner._corridor_missions_with_heights()
        assert _route(missions) == forward[::-1]
        base = slope.sample(*missions[0][0][0])
        altitude = planner.altitudeSpin.value()
        for waypoints, heights, _ground in missions:
            assert heights == [round(altitude + slope.sample(lon, lat) - base, 1)
                               for lon, lat in waypoints]

        # flypath.io keeps the route as flown and the reverse setting. It sends
        # a reversed corridor back in its original order; the plugin restores
        # the flown order.
        planner._on_preview()
        flown = list(planner._waypoints)
        payload = planner._website_payload('Reversed corridor')
        sync.validate_mission(payload)
        assert payload['settings']['reverse_route'] is True
        assert payload['waypoints'] == [[lat, lon] for lon, lat in flown]
        planner._apply_website_mission(dict(payload, waypoints=payload['waypoints'][::-1]))
        assert planner._mission_kind() == 'corridor'
        assert planner.reverseRouteCheck.isChecked() and planner.reverseRouteCheck.isEnabled()
        assert planner._waypoints == flown
    finally:
        planner.close()
        canvas.close()
        app.processEvents()


def test_2d_terrain_follow_with_reverse_route():
    try:
        importlib.import_module('qgis.PyQt.QtCore')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    with _KeepUserSettings():
        _test_2d_terrain_follow_with_reverse_route()


def _test_2d_terrain_follow_with_reverse_route():
    from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry
    app, canvas, dialog, sync = _modules()
    planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
    try:
        planner.show()
        app.processEvents()
        planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
        planner.missionTypeCombo.setCurrentText('2D Mapping')
        planner.altitudeSpin.setValue(60)
        planner.captureFullRadio.setChecked(True)
        slope = _Slope()
        planner._terrain = slope
        planner._set_survey_polygon(QgsGeometry.fromWkt(AREA_WKT),
                                    QgsCoordinateReferenceSystem('EPSG:4326'))
        planner.reverseRouteCheck.setChecked(True)
        # Turning terrain follow on keeps Reverse route.
        planner.terrainFollowCheck.setChecked(True)
        assert not planner._uses_shared_planning()
        assert planner.reverseRouteCheck.isChecked() and planner.reverseRouteCheck.isEnabled()
        reverse, _ = planner._generate_waypoints()
        planner.reverseRouteCheck.setChecked(False)
        forward, _ = planner._generate_waypoints()
        # Full automatic: the same photo waypoints, flown backwards.
        assert reverse == forward[::-1]
        planner.reverseRouteCheck.setChecked(True)
        assert planner._live_waypoints == reverse

        # Heights are measured from the new first waypoint.
        planner._on_preview()
        altitude = planner.altitudeSpin.value()
        base = slope.sample(*planner._missions[0][0])
        for waypoints, heights in zip(planner._missions, planner._preview_heights):
            assert heights == [round(altitude + slope.sample(lon, lat) - base, 1)
                               for lon, lat in waypoints]
        assert planner._preview_heights[0][0] == altitude

        # Semi automatic adds terrain waypoints along the reversed lines.
        planner.captureSemiRadio.setChecked(True)
        reverse, _ = planner._generate_waypoints()
        planner.reverseRouteCheck.setChecked(False)
        forward, _ = planner._generate_waypoints()
        planner.reverseRouteCheck.setChecked(True)
        assert reverse[0] == forward[-1] and reverse[-1] == forward[0]
        assert len(reverse) > len(planner._grid_directions()) * 2

        # flypath.io keeps the route as flown; the plugin opens it again.
        planner._on_preview()
        flown = list(planner._waypoints)
        payload = planner._website_payload('Reversed terrain 2D')
        sync.validate_mission(payload)
        assert payload['settings']['reverse_route'] is True
        assert payload['settings']['terrain_follow'] is True
        assert payload['waypoints'] == [[lat, lon] for lon, lat in flown]
        planner._apply_website_mission(dict(payload, waypoints=payload['waypoints'][::-1]))
        assert planner._mission_kind() == '2d' and planner.terrainFollowCheck.isChecked()
        assert planner.reverseRouteCheck.isChecked() and planner.reverseRouteCheck.isEnabled()
        assert planner._waypoints == flown
    finally:
        planner.close()
        canvas.close()
        app.processEvents()
