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
        from qgis.core import QgsApplication, QgsProject
        from qgis.gui import QgsMapCanvas
        from qgis.PyQt.QtGui import QShowEvent
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
        planner._planning_request = {'contract_version': 1}
        planner._planning_result = {'contract_version': 1}
        planner._on_clear_preview(reset_area=False)
        assert planner._waypoints == [] and planner._missions == []
        assert planner._planning_request is None and planner._planning_result is None

        planner._waypoints = [(1, 2), (3, 4)]
        planner._missions = [list(planner._waypoints)]
        planner._shot_spacing_m = 5
        error = module.planning_adapter.PlanningError('invalid', 'bad request')
        with patch.object(module.planning_adapter, 'plan', side_effect=error):
            assert planner._plan_shared() is None
        assert planner._waypoints == [] and planner._missions == []
        assert planner._shot_spacing_m == 0

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
        assert planner._preview_layer_ids and planner._planning_result, \
            'Loading a mission without a saved route must preview automatically'
        expected = [flight['waypoints'] for flight in
                    module.planning_adapter.consume_result(
                        planner._planning_request, planner._planning_result)]
        assert planner._missions == expected
        shared = planner._website_payload('Shared mission')
        saved_result = shared['planning_result']
        legacy = dict(mission, waypoints=mission['polygon'][:2], estimates={
            'time': '1 min', 'distance': '0.23 km', 'photos': '11',
            'batteries': '1', 'area': '0.96 ha',
            'statistics': {'strip_count': 12}})
        planner._apply_website_mission(legacy)
        assert planner.photosLabel.text() == '11', 'Show saved estimates on load'
        assert planner.waypointsLabel.text() == '2'
        assert planner.linesLabel.text() == '12', 'Show saved line count on load'
        assert planner._waypoints == [tuple(reversed(p)) for p in legacy['waypoints']]
        loaded_route = deepcopy(planner._waypoints)
        with patch.object(planner, '_generate_waypoints',
                          return_value=(loaded_route, 5.0)) as generate:
            planner._on_preview()
        generate.assert_not_called()
        assert planner._waypoints == loaded_route, 'Preview must not replace a saved route'

        full_legacy = deepcopy(mission)
        full_legacy['settings'].update(capture_mode='full', front_overlap=50,
                                       auto_direction=True, direction=90)
        full_legacy['waypoints'] = mission['polygon'][:2]
        planner._apply_website_mission(full_legacy)
        assert planner._planning_result, \
            'A legacy Full-auto route must be regenerated from its settings'
        assert len(planner._waypoints) > len(full_legacy['waypoints'])
        assert planner._planning_request['direction']['mode'] == 'automatic'
        assert planner.directionSpin.value() == \
            planner._planning_result['resolved_direction']['value_deg']
        assert len(planner._missions) >= full_legacy['settings']['split_count']
        assert int(planner.photosLabel.text().replace(',', '')) >= len(planner._waypoints)

        previous_layers = list(planner._preview_layer_ids)
        planner._apply_website_mission(legacy)
        assert all(QgsProject.instance().mapLayer(lid) is None
                   for lid in previous_layers), 'Repeated loads replace the preview'
        ambiguous = deepcopy(mission)
        del ambiguous['settings']['split_enabled']
        planner._apply_website_mission(ambiguous)
        assert not planner._preview_layer_ids and not planner._waypoints
        assert planner._split_choice_required, 'Do not guess an older splitting choice'
        older = deepcopy(shared)
        older['planning_result']['engine_version'] = '0.3.0'
        planner._apply_website_mission(older)
        assert planner._unsupported_saved_plan and planner._saved_plan_locked
        planner._apply_website_mission(shared)
        assert planner._planning_result == saved_result
        assert planner._saved_plan_locked and not planner._saved_plan_dirty
        with patch.object(planner, '_generate_waypoints') as generate:
            planner._on_preview()
        generate.assert_not_called()
        assert planner._planning_result == saved_result
        planner.altitudeSpin.setValue(81)
        assert planner._saved_plan_dirty
        assert planner.previewBtn.text() == 'Regenerate on Map'
        with patch.object(module.QMessageBox, 'warning') as warning:
            assert planner._on_send_to_website() is False
            planner._on_export()
        assert warning.call_count == 2

        # Terrain generation must resolve Auto, too, instead of using the
        # stale manual heading imported alongside a saved automatic route.
        terrain = deepcopy(legacy)
        terrain['settings'] = dict(legacy['settings'], terrain_follow=True,
                                   auto_direction=True, direction=90)
        planner._apply_website_mission(terrain)
        with patch.object(module, 'find_optimal_direction', return_value=0), \
                patch.object(module, 'generate_flight_grid',
                             return_value=(loaded_route, 5.0)) as grid, \
                patch.object(planner, '_apply_terrain',
                             side_effect=lambda route: (route, None)):
            planner._generate_waypoints()
        assert grid.call_args.kwargs['direction_deg'] == 0
        assert planner.autoDirectionBtn.isChecked()

        # Website loads happen while the My missions tab hides the planner.
        # Showing the planner must reveal stats for a restored local route too.
        planner._planning_result = None
        planner._live_waypoints = None
        planner._waypoints = loaded_route
        with patch.object(planner, '_show_hud') as show_hud:
            planner.showEvent(QShowEvent())
        show_hud.assert_called_once()

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
