"""Reverse route for routes planned outside the engine (no network)."""

import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
        assert planner._planning.result['operation'] == 'plan_corridor'
        assert flat_wps == [(row['position']['longitude_deg'], row['position']['latitude_deg'])
                            for row in planner._planning.result['route']['waypoints']]
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

        # A provenance-bearing route is sent and restored in its flown order.
        planner._on_preview()
        flown = list(planner._waypoints)
        payload = planner._website_payload('Reversed corridor')
        sync.validate_mission(payload)
        assert payload['settings']['reverse_route'] is True
        assert payload['waypoints'] == [[lat, lon] for lon, lat in flown]
        planner._apply_website_mission(payload)
        assert planner._mission_kind() == 'corridor'
        assert planner.reverseRouteCheck.isChecked() and planner.reverseRouteCheck.isEnabled()
        assert planner._waypoints == flown
    finally:
        planner.close()
        canvas.close()
        app.processEvents()


def test_shared_corridor_import_exports_saved_flights_and_terrain():
    try:
        importlib.import_module('qgis.PyQt.QtCore')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    with _KeepUserSettings():
        from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry
        app, canvas, dialog, sync = _modules()
        planning_adapter = importlib.import_module(dialog.__package__ + '.planning_adapter')
        mission_export = importlib.import_module(dialog.__package__ + '.mission_export')
        planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            planner.show()
            app.processEvents()
            planner.missionTypeCombo.setCurrentText('Corridor Mapping')
            planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
            planner.captureFullRadio.setChecked(True)
            planner.altitudeSpin.setValue(80)
            planner.bufferSpin.setValue(30)
            planner._set_survey_line(QgsGeometry.fromWkt(LINE_WKT),
                                     QgsCoordinateReferenceSystem('EPSG:4326'))
            planner._corridor_breaks = {(0, 1)}
            planner.splitCheck.setChecked(True)
            planner.splitSpin.setValue(2)
            planner._terrain = _Slope()
            planner.terrainFollowCheck.setChecked(True)
            planner.terrainToleranceSpin.setValue(5)
            planner.launchOffsetSpin.setValue(7)
            planner.reverseRouteCheck.setChecked(True)
            planner._on_preview()
            request, result = planner._planning.request, planner._planning.result
            assert request['operation'] == 'plan_corridor'
            assert request['break_indices'] == [1]
            assert request['width_m'] == 60
            assert len(result['flights']) == 2
            payload = planner._website_payload('Shared corridor')
            sync.validate_mission(payload)

            def no_sample(*_args):
                raise AssertionError('Saved terrain must not be sampled again.')

            planner._terrain.sample = no_sample
            with patch.object(planner, '_plan_shared', side_effect=AssertionError('regeneration')):
                planner._apply_website_mission(payload)
                assert planner._planning.request == request
                assert planner._planning.result == result
                assert planner._corridor_breaks == {(0, 1)}
                assert planner._preview_ground
                expected = planning_adapter.consume_result(request, result)
                assert planner._missions == [flight['waypoints'] for flight in expected]

                exported = []
                planner._confirm_low_altitude_export = lambda: True
                planner._export_local = lambda _name, missions, actions, settings: exported.append(
                    (missions, actions, settings))
                for index in range(planner.destCombo.count()):
                    if planner.destCombo.itemData(index) != 'rc':
                        planner.destCombo.setCurrentIndex(index)
                        break
                planner._on_export()
            assert len(exported) == 1
            missions, actions, settings = exported[0]
            assert [mission[0] for mission in missions] == [flight['waypoints'] for flight in expected]
            assert actions == [flight['actions'] for flight in expected]
            heights = planning_adapter.corridor_flights_with_heights(
                request, result, takeoff_reference=payload['settings']['takeoff_reference'])
            assert [mission[1] for mission in missions] == [flight['heights'] for flight in heights]
            assert [mission[2] for mission in missions] == [flight['ground'] for flight in heights]
            assert [setting.heights for setting in settings] == [
                tuple(flight['heights']) for flight in heights]
            written = []
            with patch.object(mission_export, 'write_mission', side_effect=lambda _drone, spec, _path: written.append(spec)):
                mission_export.write_kmz('unused.kmz', settings[0], missions[0][0],
                                         'Shared corridor', heights=missions[0][1],
                                         actions=actions[0])
            assert written[0].waypoints == expected[0]['waypoints']
            assert written[0].actions == actions[0]
            assert settings[0].launch_offset_m == 7
            assert all(abs(actual - (height - 7)) < 1e-9
                       for actual, height in zip(written[0].heights, heights[0]['heights']))
        finally:
            planner.close()
            canvas.close()
            app.processEvents()


def test_flat_saved_corridor_exports_its_imported_route():
    try:
        importlib.import_module('qgis.PyQt.QtCore')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    with _KeepUserSettings():
        app, canvas, dialog, _sync = _modules()
        planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            planner.show()
            app.processEvents()
            mission = {
                'drone_model': 'mini4pro',
                'polygon': [[51.1, 8.1], [51.1, 8.102]],
                'waypoints': [[51.1002, 8.1003], [51.1002, 8.1017]],
                'settings': {
                    'mapping_style': 'corridor', 'corridor_width': 60,
                    'capture_mode': 'semi', 'altitude': 80, 'speed': 8,
                    'side_overlap': 70, 'direction': 60,
                    'split_enabled': False, 'split_count': 1,
                    'split_max_wp': 70, 'terrain_follow': False,
                },
            }
            planner._apply_website_mission(mission)
            saved = [(lon, lat) for lat, lon in mission['waypoints']]
            assert planner._waypoints == saved
            assert planner._planning.export_issue() is None
            observed = []
            planner._export_local = lambda _name, missions, _actions, _settings: observed.extend(missions)
            for index in range(planner.destCombo.count()):
                if planner.destCombo.itemData(index) != 'rc':
                    planner.destCombo.setCurrentIndex(index)
                    break
            planner._confirm_low_altitude_export = lambda: True
            planner._on_export()
            assert observed
            assert [point for points, *_ in observed for point in points] == saved
            for capture, terrain in (('full', False), ('semi', True)):
                blocked = {**mission, 'settings': {**mission['settings'],
                                                  'capture_mode': capture,
                                                  'terrain_follow': terrain}}
                planner._apply_website_mission(blocked)
                observed.clear()
                with patch.object(dialog.QMessageBox, 'warning') as warning:
                    planner._on_export()
                assert warning.called and not observed
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
