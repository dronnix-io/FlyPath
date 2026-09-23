"""Shared planning adapter and consumer serialization checks."""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from FlyPath import planning_adapter  # noqa: E402
from FlyPath.hardware import registry  # noqa: E402
from FlyPath.wpml import MissionSpec, write_mission  # noqa: E402


AREA = {
    'exterior': [
        {'latitude_deg': 51.0, 'longitude_deg': -114.0},
        {'latitude_deg': 51.0, 'longitude_deg': -113.998},
        {'latitude_deg': 51.002, 'longitude_deg': -113.998},
        {'latitude_deg': 51.002, 'longitude_deg': -114.0},
    ],
    'holes': [],
}


def request(**overrides):
    values = dict(
        survey_area=AREA, drone_profile_id='mini3pro', altitude_m=80,
        speed_m_s=8, side_overlap_ratio=.7, margin_m=0,
        automatic_direction=False, direction_deg=70, capture_mode='full',
        front_overlap_ratio=.8, turn_style='curved',
        finish_action='Hover in place', split_enabled=True,
        requested_flights=1, max_waypoints_per_flight=70,
    )
    values.update(overrides)
    return planning_adapter.build_request(**values)


def test_plan_result_drives_flights_and_actions():
    planned_request = request()
    result = planning_adapter.plan(planned_request)
    flights = planning_adapter.consume_result(planned_request, result)
    assert len(flights) == result['statistics']['battery_count']
    assert sum(action['type'] == 'take_photo'
               for flight in flights for action in flight['actions']) \
        == result['statistics']['photo_count']
    assert flights[0]['actions'][:3] == [
        {'type': 'rotate_camera', 'waypoint_index': 0, 'pitch_deg': -90},
        {'type': 'hover', 'waypoint_index': 0, 'duration_s': 3.0},
        {'type': 'take_photo', 'waypoint_index': 0},
    ]


def test_locations_survive_the_adapter_request():
    locations = {'shared': {
        'launch': {'latitude_deg': 51, 'longitude_deg': -114},
        'home': {'latitude_deg': 51, 'longitude_deg': -114},
    }}
    planned_request = request(locations=locations)
    assert planned_request['locations'] == locations
    assert planning_adapter.plan(planned_request)['statistics']['estimates_complete']


def test_reverse_route_reverses_engine_waypoints():
    normal = planning_adapter.plan(request())
    reversed_result = planning_adapter.plan(request(reverse_route=True))
    normal_points = [row['position'] for row in normal['route']['waypoints']]
    reversed_points = [row['position']
                       for row in reversed_result['route']['waypoints']]
    assert reversed_points == normal_points[::-1]


def test_saved_result_mismatch_is_rejected():
    planned_request = request()
    result = planning_adapter.plan(planned_request)
    changed = deepcopy(result)
    changed['flights'][0]['route_end_waypoint_index'] -= 1
    try:
        planning_adapter.consume_result(planned_request, changed)
    except ValueError:
        pass
    else:
        raise AssertionError('inconsistent saved flights must not be exported')


def test_older_result_keeps_view_only_provenance():
    planned_request = request()
    result = planning_adapter.plan(planned_request)
    result['engine_version'] = '0.3.0'
    mission = {
        'drone_model': 'mini3pro',
        'polygon': [[point['latitude_deg'], point['longitude_deg']]
                    for point in AREA['exterior']],
        'waypoints': [[row['position']['latitude_deg'],
                       row['position']['longitude_deg']]
                      for row in result['route']['waypoints']],
        'settings': {
            'mapping_style': '2d', 'capture_mode': 'full',
            'front_overlap': 80, 'altitude': 80, 'speed': 8,
            'side_overlap': 70, 'margin': 0, 'flight_path': 'curved',
            'finish_action': 'noAction', 'cross_hatch': False,
            'reverse_route': False, 'terrain_follow': False,
            'split_enabled': True, 'split_count': 1, 'split_max_wp': 70,
            'auto_direction': False, 'direction': 70,
        },
        'planning_request': planned_request,
        'planning_result': result,
    }
    planning_adapter.validate_mission_provenance(mission)
    try:
        planning_adapter.consume_result(planned_request, result)
    except ValueError:
        pass
    else:
        raise AssertionError('older engine results must remain view-only')


def test_engine_actions_are_written_to_wpml():
    planned_request = request()
    result = planning_adapter.plan(planned_request)
    flight = planning_adapter.consume_result(planned_request, result)[0]
    spec = MissionSpec(
        waypoints=flight['waypoints'], altitude_m=80, speed_ms=8,
        finish_action='Hover in place', rc_lost_action='Return to Home',
        capture_mode='full', actions=flight['actions'])
    path = str(Path(tempfile.mkdtemp()) / 'mission.kmz')
    write_mission(registry.get('DJI Mini 3 Pro'), spec, path)
    with zipfile.ZipFile(path) as archive:
        wpml = archive.read('wpmz/waylines.wpml').decode('utf-8')
    assert wpml.count('<wpml:actionActuatorFunc>takePhoto</wpml:actionActuatorFunc>') \
        == flight['flight']['photo_count']
    assert '<wpml:hoverTime>3.0</wpml:hoverTime>' in wpml


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_') and callable(value)]
    for test in tests:
        test()
    print('%d planning adapter checks passed' % len(tests))
