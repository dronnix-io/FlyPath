"""Orbit website roundtrip through the installed QGIS planner (no network)."""

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


def test_orbit_roundtrip_and_dirty_route():
    try:
        from qgis.core import QgsApplication, QgsProject
        from qgis.gui import QgsMapCanvas
        package = Path(__file__).resolve().parents[1].name
        dialog = importlib.import_module(package + '.flypath_dialog')
        sync = importlib.import_module(package + '.flypath_sync')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    canvas = QgsMapCanvas()
    planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
    try:
        planner.show()
        app.processEvents()
        planner.missionTypeCombo.setCurrentText('Orbit (3D Model)')
        planner.droneModelCombo.setCurrentText('DJI Mini 4 Pro')
        planner.captureSemiRadio.setChecked(True)
        planner.orbitRadiusSpin.setValue(45)
        planner.orbitTiltSpin.setValue(-40)
        planner.altitudeSpin.setValue(30)
        planner.reverseRouteCheck.setChecked(True)
        planner.set_orbit_centre(-114, 51)
        planner._on_preview()
        mission = planner._website_payload('Orbit')
        sync.validate_mission(mission)
        assert mission['polygon'] == [[51, -114]]
        assert mission['settings']['orbit_radius'] == 45
        assert mission['settings']['orbit_tilt'] == -40
        assert mission['planning_request']['direction'] == 'counterclockwise'
        assert planner.frontOverlapStatLabel.text() == planner.frontOverlapLabel.text().split()[0] + '%'
        saved_route = deepcopy(mission['planning_result'])
        saved_waypoints = deepcopy(mission['waypoints'])
        with patch.object(dialog.planning_adapter, 'plan', side_effect=AssertionError('regenerated')):
            adjusted, note = planner._apply_website_mission(mission)
        assert not adjusted and not note
        assert planner._planning.result == saved_route
        assert planner._export_settings().headings == tuple(
            row['heading_deg'] for row in saved_route['route']['waypoints'])
        assert planner._website_payload('Orbit again')['waypoints'] == saved_waypoints
        assert planner._website_payload('Orbit again')['planning_result'] == saved_route
        assert planner.frontOverlapStatLabel.text() == planner.frontOverlapLabel.text().split()[0] + '%'
        older = deepcopy(mission)
        older['planning_result']['engine_version'] = '1.1.0'
        try:
            planner._apply_website_mission(older)
        except sync.FlypathSyncError:
            pass
        else:
            raise AssertionError('Orbit result from a pre-Orbit engine accepted')
        planner.orbitRadiusSpin.setValue(46)
        assert planner._planning.save_requires_regeneration()
        bad = deepcopy(mission)
        bad['settings']['orbit_radius'] = 2001
        before = (planner._orbit_centre, planner.orbitRadiusSpin.value(),
                  deepcopy(planner._planning.result), sorted(QgsProject.instance().mapLayers()))
        try:
            planner._apply_website_mission(bad)
        except sync.FlypathSyncError:
            pass
        else:
            raise AssertionError('Invalid orbit accepted')
        assert before == (planner._orbit_centre, planner.orbitRadiusSpin.value(),
                          planner._planning.result, sorted(QgsProject.instance().mapLayers()))
        # Failure after controls and map layers change must restore the current orbit.
        changed = deepcopy(mission)
        changed['polygon'] = [[51.001, -114.001]]
        changed['settings']['orbit_radius'] = 70
        changed.pop('planning_request')
        changed.pop('planning_result')
        with patch.object(planner, '_restore_imported_route', side_effect=RuntimeError('failed')):
            try:
                planner._apply_website_mission(changed)
            except sync.FlypathSyncError:
                pass
            else:
                raise AssertionError('Failed import accepted')
        assert before == (planner._orbit_centre, planner.orbitRadiusSpin.value(),
                          planner._planning.result, sorted(QgsProject.instance().mapLayers()))

        planner.captureFullRadio.setChecked(True)
        planner._on_preview()
        full = planner._website_payload('Full orbit')
        assert full['planning_request']['capture']['side_overlap_ratio'] == (
            full['settings']['side_overlap'] / 100)
        planner._apply_website_mission(full)
        assert planner._planning.result == full['planning_result']
        assert planner._export_settings().headings
        legacy = deepcopy(mission)
        legacy.pop('planning_request')
        legacy.pop('planning_result')
        planner._apply_website_mission(legacy)
        assert planner._waypoints == [(lon, lat) for lat, lon in saved_waypoints]
        assert planner._planning.save_requires_regeneration()
    finally:
        planner.close()
        canvas.close()
        app.processEvents()
