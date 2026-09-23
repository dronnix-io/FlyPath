"""Run with QGIS Python: python tests/test_website_settings.py (no network)."""
import importlib
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_settings_round_trip():
    try:
        from qgis.core import QgsApplication
        from qgis.gui import QgsMapCanvas
        module = importlib.import_module(Path(__file__).resolve().parents[1].name + '.flypath_dialog')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    canvas = QgsMapCanvas()
    planner = module.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
    mission = {
        'drone_model': 'mini3pro',
        'polygon': [[51.01, -114.02], [51.01, -114.015],
                    [51.013, -114.015], [51.013, -114.02]],
        'waypoints': [[51.01, -114.02], [51.013, -114.015]],
        'settings': {
            'mapping_style': '2d', 'capture_mode': 'semi',
            'altitude': 80, 'speed': 8, 'side_overlap': 70,
            'direction': 95, 'auto_direction': True,
            'split_enabled': False, 'split_count': 2, 'split_max_wp': 70,
        },
    }
    mission['drone_model'] = module.registry.get('DJI Mini 3 Pro').website_code
    try:
        planner._apply_website_mission(mission)
        saved = planner._website_settings()
        assert saved['auto_direction'] is True, saved
        assert saved['direction'] == 95, saved
        assert saved['split_enabled'] is False, saved
        assert planner.splitSpin.value() == 1
        assert planner._split_overridden, 'Imported split-off must not track battery defaults'

        mission['settings'].update(auto_direction=False, split_enabled=True, split_count=3)
        planner._apply_website_mission(mission)
        saved = planner._website_settings()
        assert saved['direction'] == 95 and saved['auto_direction'] is False
        assert saved['split_enabled'] is True and saved['split_count'] == 3, saved

        mission['settings']['auto_direction'] = True
        planner._apply_website_mission(mission)
        planner.speedSpin.setValue(7)
        assert planner._website_settings()['auto_direction'] is True
        planner.directionSpin.setValue(40)
        assert planner._website_settings()['auto_direction'] is False
        assert planner._website_settings()['direction'] == 40
        planner.autoDirectionBtn.click()
        assert planner._website_settings()['auto_direction'] is True
        planner.autoDirectionBtn.click()
        assert planner._website_settings()['auto_direction'] is False

        mission['settings'] = {
            'mapping_style': '2d', 'capture_mode': 'semi',
            'altitude': 80, 'speed': 8, 'side_overlap': 70,
            'margin': 0, 'direction': 95, 'auto_direction': False,
            'flight_path': 'curved', 'finish_action': 'goHome',
            'cross_hatch': False, 'reverse_route': True,
            'terrain_follow': False, 'split_enabled': False,
            'split_count': 1, 'split_max_wp': 70,
        }
        area = {'exterior': [
            {'latitude_deg': lat, 'longitude_deg': lon}
            for lat, lon in mission['polygon']
        ], 'holes': []}
        request = module.planning_adapter.build_request(
            survey_area=area, drone_profile_id=mission['drone_model'],
            altitude_m=80, speed_m_s=8, side_overlap_ratio=.7,
            margin_m=0, automatic_direction=False, direction_deg=95,
            capture_mode='semi', front_overlap_ratio=.7,
            turn_style='curved', finish_action='Return to Home',
            split_enabled=False, requested_flights=1,
            max_waypoints_per_flight=70, reverse_route=True,
        )
        result = module.planning_adapter.plan(request)
        mission['planning_request'] = request
        mission['planning_result'] = result
        mission['waypoints'] = [
            [row['position']['latitude_deg'], row['position']['longitude_deg']]
            for row in result['route']['waypoints']
        ]
        planner._apply_website_mission(mission)
        assert planner.reverseRouteCheck.isChecked()
        assert planner._website_settings()['reverse_route'] is True

        # Repeated loads must clear the previous mission's split choice.
        mission['settings']['reverse_route'] = False
        mission['planning_request'] = {}
        mission['planning_result'] = {}
        mission['settings'].update(auto_direction=False, split_enabled=False)
        planner._apply_website_mission(mission)
        planner._apply_website_mission(mission)
        planner._apply_split_default(10, 4)
        assert planner.splitSpin.value() == 1
    finally:
        planner.close()
        canvas.close()
        app.processEvents()


if __name__ == '__main__':
    test_settings_round_trip()
    print('Website settings round-trip check passed')
