"""QGIS state checks for shared planning and terrain export safety."""

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


def test_planning_dialog_state():
    try:
        from qgis.core import QgsApplication
        from qgis.gui import QgsMapCanvas
        module = importlib.import_module(
            Path(__file__).resolve().parents[1].name + '.flypath_dialog')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    canvas = QgsMapCanvas()
    planner = module.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
    try:
        assert planner.splitCheck.isChecked(), 'new missions enable splitting'
        planner._waypoints = [(1, 2), (3, 4)]
        planner._missions = [list(planner._waypoints)]
        planner._flight_actions = [{'type': 'take_photo'}]
        planner._planning_request = {'contract_version': 1}
        planner._planning_result = {'contract_version': 1}
        planner._on_clear_preview(reset_area=False)
        assert planner._waypoints == [] and planner._missions == []
        assert planner._planning_request is None and planner._planning_result is None

        planner.terrainFollowCheck.setChecked(True)
        planner._terrain = SimpleNamespace(
            sample=lambda *_: (_ for _ in ()).throw(module._TerrainError('offline')),
            clear=lambda: None)
        with patch.object(module.QMessageBox, 'warning'):
            route, elevations = planner._apply_terrain([(0, 0), (0, .01)])
        assert route == [(0, 0), (0, .01)] and elevations is None
        assert planner._terrain_failed, 'flat fallback must be marked unsafe to export'

        # A shared result survives load verbatim, then any settings edit blocks
        # both save and export until Preview explicitly regenerates it.
        planner.terrainFollowCheck.setChecked(False)
        mission = {
            'drone_model': 'mini3pro',
            'polygon': [[51.01, -114.02], [51.01, -114.015],
                        [51.013, -114.015], [51.013, -114.02]],
            'settings': {
                'mapping_style': '2d', 'capture_mode': 'semi',
                'flight_path': 'curved', 'finish_action': 'goHome',
                'rc_lost_action': 'goBack', 'altitude': 80, 'speed': 8,
                'side_overlap': 70, 'direction': 95,
                'auto_direction': False, 'margin': 0,
                'cross_hatch': False, 'terrain_follow': False,
                'split_enabled': True, 'split_count': 1,
                'split_max_wp': 70,
            },
        }
        planner._apply_website_mission(mission)
        planner._on_preview()
        shared = planner._website_payload('Shared mission')
        saved_result = shared['planning_result']
        older = deepcopy(shared)
        older['planning_result']['engine_version'] = '0.3.0'
        planner._apply_website_mission(older)
        assert planner._unsupported_saved_plan and planner._saved_plan_locked
        planner._apply_website_mission(shared)
        assert planner._planning_result == saved_result
        assert planner._saved_plan_locked and not planner._saved_plan_dirty
        planner.altitudeSpin.setValue(81)
        assert planner._saved_plan_dirty
        with patch.object(module.QMessageBox, 'warning') as warning:
            assert planner._on_send_to_website() is False
            planner._on_export()
        assert warning.call_count == 2

        # A DEM error raised by generation during export must not reach a writer.
        planner._saved_plan_locked = False
        planner._saved_plan_dirty = False
        planner._planning_request = None
        planner._planning_result = None
        planner._waypoints = []
        planner.terrainFollowCheck.blockSignals(True)
        planner.terrainFollowCheck.setChecked(True)
        planner.terrainFollowCheck.blockSignals(False)
        planner._terrain_failed = False

        def fail_during_generation():
            planner._terrain_failed = True
            return [(0, 0), (0, .01)], 5.0

        with patch.object(planner, '_generate_waypoints', side_effect=fail_during_generation), \
                patch.object(planner, '_export_local') as export, \
                patch.object(module.QMessageBox, 'warning') as warning:
            planner._on_export()
        export.assert_not_called()
        assert warning.call_args.args[1] == 'Terrain Export Blocked'
    finally:
        planner.close()
        canvas.close()
        app.processEvents()


if __name__ == '__main__':
    test_planning_dialog_state()
    print('Planning dialog state check passed')
