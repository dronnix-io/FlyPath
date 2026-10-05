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
    supported, _ = planning_adapter.validate_mission_provenance(mission)
    assert not supported
    try:
        planning_adapter.consume_result(planned_request, result)
    except ValueError:
        pass
    else:
        raise AssertionError('older engine results must remain view-only')

    for version in ('0.4.0', '1.0.0', '1.1.0', '1.2.0'):
        result['engine_version'] = version
        saved = deepcopy(mission)
        supported, _ = planning_adapter.validate_mission_provenance(mission)
        assert supported
        assert planning_adapter.consume_result(planned_request, result)
        assert mission == saved
        result['statistics']['known_distance_m'] += 1
        try:
            planning_adapter.validate_mission_provenance(mission)
        except ValueError:
            pass
        else:
            raise AssertionError('compatible engine versions must still reject forged results')
        result['statistics']['known_distance_m'] -= 1


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


def orbit_request(**overrides):
    values = dict(
        centre=(-114.0, 51.0), drone_profile_id='mini4pro', radius_m=30,
        altitude_m=25, gimbal_pitch_deg=-35, clockwise=True, speed_m_s=3,
        capture_mode='full', side_overlap_ratio=.9, finish_action='Return to Home',
        max_waypoints_per_flight=200)
    values.update(overrides)
    return planning_adapter.build_orbit_request(**values)


def test_orbit_request_routes_to_the_orbit_engine():
    planned = orbit_request()
    assert planned['operation'] == 'plan_orbit' and planned['mapping_style'] == 'orbit'
    assert planned['centre'] == {'latitude_deg': 51.0, 'longitude_deg': -114.0}
    assert planned['capture'] == {'mode': 'full_auto', 'side_overlap_ratio': .9}
    semi = orbit_request(capture_mode='semi', clockwise=False)
    assert semi['capture'] == {'mode': 'semi_auto'}
    assert semi['direction'] == 'counterclockwise'
    result = planning_adapter.plan(planned)
    assert result['mapping_style'] == 'orbit'


def test_orbit_headings_reach_the_flight_and_the_wpml():
    planned = orbit_request()
    result = planning_adapter.plan(planned)
    flight = planning_adapter.consume_result(planned, result)[0]
    assert len(flight['headings']) == len(flight['waypoints'])
    assert flight['headings'] == [row['heading_deg'] for row in result['route']['waypoints']]
    spec = MissionSpec(
        waypoints=flight['waypoints'], altitude_m=25, speed_ms=3,
        finish_action='Return to Home', rc_lost_action='Return to Home',
        gimbal_pitch=-35, capture_mode='full', actions=flight['actions'],
        headings=flight['headings'])
    path = str(Path(tempfile.mkdtemp()) / 'orbit.kmz')
    write_mission(registry.get('DJI Mini 4 Pro'), spec, path)
    with zipfile.ZipFile(path) as archive:
        wpml = archive.read('wpmz/waylines.wpml').decode('utf-8')
    waypoints = len(flight['waypoints'])
    assert wpml.count('<wpml:waypointHeadingMode>smoothTransition</wpml:waypointHeadingMode>') == waypoints
    assert wpml.count('<wpml:actionActuatorFunc>takePhoto</wpml:actionActuatorFunc>') == waypoints
    assert '<wpml:gimbalPitchRotateAngle>-35.0</wpml:gimbalPitchRotateAngle>' in wpml


def test_2d_flights_have_no_headings():
    planned_request = request()
    flight = planning_adapter.consume_result(planned_request, planning_adapter.plan(planned_request))[0]
    assert flight['headings'] is None


def test_forged_orbit_headings_are_rejected():
    planned = orbit_request()
    result = planning_adapter.plan(planned)
    result['route']['waypoints'][3]['heading_deg'] = 400
    try:
        planning_adapter.consume_result(planned, result)
    except ValueError:
        pass
    else:
        raise AssertionError('out of range headings must be rejected')


def test_orbit_provenance_verifies_result_and_settings():
    planned = orbit_request()
    result = planning_adapter.plan(planned)
    mission = {
        'drone_model': 'mini4pro', 'polygon': [[51.0, -114.0]],
        'planning_request': planned, 'planning_result': result,
        'settings': {
            'mapping_style': 'orbit', 'orbit_radius': 30, 'orbit_tilt': -35,
            'altitude': 25, 'speed': 3, 'reverse_route': False,
            'finish_action': 'goHome', 'capture_mode': 'full',
            'side_overlap': 90, 'split_max_wp': 200,
        },
    }
    saved = deepcopy(mission)
    supported, flights = planning_adapter.validate_mission_provenance(mission)
    assert supported and len(flights) == 1 and flights[0]['headings']
    assert mission == saved
    changes = [deepcopy(saved) for _ in range(3)]
    changes[0]['planning_result']['route']['waypoints'][3]['heading_deg'] += 1
    changes[1]['settings']['orbit_radius'] += 1
    changes[2]['planning_result']['drone_profile_id'] = 'mini3pro'
    for changed in changes:
        try:
            planning_adapter.validate_mission_provenance(changed)
        except ValueError:
            pass
        else:
            raise AssertionError('tampered orbit provenance accepted')


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_') and callable(value)]
    for test in tests:
        test()
    print('%d planning adapter checks passed' % len(tests))
