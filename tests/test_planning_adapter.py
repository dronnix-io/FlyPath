"""Shared planning adapter and consumer serialization checks."""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import zipfile
from unittest.mock import patch
import pytest

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


@pytest.mark.parametrize('pattern,nadir', [('fast', True), ('fast', False), ('full', True), ('full', False)])
@pytest.mark.parametrize('engine_version', ['1.4.0', '1.4.1', '2.0.0', '2.1.0'])
def test_oblique_sync_provenance_keeps_passes_and_rejects_changed_settings(pattern, nadir, engine_version):
    from FlyPath.flypath_sync import validate_mission, FlypathSyncError

    planned = request(capture_mode='semi')
    planned.pop('cross_hatch')
    planned.update(operation='plan_oblique', mapping_style='oblique', pattern=pattern,
                   gimbal_pitch_deg=-45, include_nadir=nadir)
    planned['contract_version'] = 2 if engine_version in ('2.0.0', '2.1.0') else 1
    result = planning_adapter.plan(planned, engine_version=engine_version)
    mission = {
        'drone_model': 'mini3pro',
        'polygon': [[p['latitude_deg'], p['longitude_deg']] for p in AREA['exterior']],
        'waypoints': [[w['position']['latitude_deg'], w['position']['longitude_deg']]
                      for w in result['route']['waypoints']],
        'settings': {
            'mapping_style': 'oblique', 'capture_mode': 'semi', 'altitude': 80,
            'speed': 8, 'side_overlap': 70, 'margin': 0, 'flight_path': 'curved',
            'finish_action': 'noAction', 'terrain_follow': False, 'cross_hatch': False,
            'reverse_route': False, 'split_enabled': True, 'split_count': 1,
            'split_max_wp': 70, 'auto_direction': False, 'direction': 70,
            'oblique_pattern': pattern, 'oblique_pitch': -45, 'oblique_nadir': nadir,
        },
        'planning_request': planned, 'planning_result': result,
    }
    validate_mission(mission)
    saved = deepcopy(mission)
    supported, flights = planning_adapter.validate_mission_provenance(mission)
    assert supported and mission == saved
    assert planning_adapter.needs_geometry_update(result) == (pattern == 'fast' and engine_version == '1.4.0')
    rows = result['route']['waypoints']
    for flight in flights:
        start = flight['flight']['route_start_waypoint_index']
        end = flight['flight']['route_end_waypoint_index']
        survey_pass = result['oblique']['passes'][rows[start]['pass_index']]
        assert rows[start]['pass_index'] == rows[end]['pass_index']
        assert set(flight['gimbal_pitches']) == {survey_pass['gimbal_pitch_deg']}
        assert flight['headings'] is None if survey_pass['heading_deg'] is None else (
            set(flight['headings']) == {survey_pass['heading_deg']})
    for field, value in [('oblique_pitch', -50), ('oblique_nadir', not nadir),
                         ('oblique_pattern', 'full' if pattern == 'fast' else 'fast')]:
        changed = deepcopy(mission)
        changed['settings'][field] = value
        with pytest.raises(ValueError):
            planning_adapter.validate_mission_provenance(changed)
    changed = deepcopy(mission)
    changed['planning_result']['route']['waypoints'][0]['heading_deg'] = 90
    with pytest.raises(ValueError):
        planning_adapter.validate_mission_provenance(changed)
    for field, value in [('terrain_follow', True), ('cross_hatch', True),
                         ('oblique_nadir', 1), ('oblique_pitch', -90)]:
        changed = deepcopy(mission)
        changed['settings'][field] = value
        with pytest.raises(FlypathSyncError):
            validate_mission(changed)
    changed = deepcopy(mission)
    changed.pop('planning_result')
    with pytest.raises(FlypathSyncError):
        validate_mission(changed)


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
    planned_request['contract_version'] = 1
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


RINGS = [(30, 25, -20), (30, 40, -35), (20, 55, -60)]


def ring_request(rings=RINGS, **overrides):
    return orbit_request(radius_m=None, altitude_m=None, gimbal_pitch_deg=None,
                         rings=rings, **overrides)


def test_one_listed_ring_is_the_single_ring_request():
    # flypath.io and earlier plugins verify single-ring orbits by this request.
    assert ring_request([(30, 25, -35)]) == orbit_request()
    planned = ring_request()
    assert 'radius_m' not in planned and len(planned['rings']) == 3
    assert planned['rings'][2] == {'radius_m': 20, 'altitude_m': 55, 'gimbal_pitch_deg': -60}
    single = orbit_request()
    flight = planning_adapter.consume_result(single, planning_adapter.plan(single))[0]
    assert flight['heights'] is None and flight['gimbal_pitches'] is None


def test_multi_ring_flights_carry_each_ring_altitude_and_tilt():
    for capture_mode in ('full', 'semi'):
        planned = ring_request(capture_mode=capture_mode)
        result = planning_adapter.plan(planned)
        rows = result['route']['waypoints']
        flight = planning_adapter.consume_result(planned, result)[0]
        assert len(flight['heights']) == len(flight['waypoints']) == len(rows)
        assert flight['heights'] == [RINGS[row['ring_index']][1] for row in rows]
        assert flight['gimbal_pitches'] == [RINGS[row['ring_index']][2] for row in rows]
        assert [row['ring_index'] for row in rows] == sorted(row['ring_index'] for row in rows)


def test_split_orbit_flights_start_at_their_ring_or_share_a_seam():
    planned = ring_request(split_enabled=True, requested_flights=3)
    assert planned['split'] == {'enabled': True, 'requested_flights': 3,
                                'max_waypoints_per_flight': 200}
    assert orbit_request()['split'] == {'enabled': False, 'max_waypoints_per_flight': 200}
    result = planning_adapter.plan(planned)
    flights = planning_adapter.consume_result(planned, result)
    assert len(flights) == 3
    for flight, (_radius, altitude, tilt) in zip(flights, RINGS):
        assert flight['heights'] == [altitude] * len(flight['waypoints'])
        assert flight['gimbal_pitches'] == [tilt] * len(flight['waypoints'])
        assert len(flight['headings']) == len(flight['waypoints'])
    # A gap that is not a ring boundary is refused.
    broken = deepcopy(result)
    broken['flights'][1]['route_start_waypoint_index'] += 1
    try:
        planning_adapter.consume_result(planned, broken)
    except ValueError:
        pass
    else:
        raise AssertionError('a gap inside a ring was accepted')
    # One ring larger than the limit is cut into parts that share their seams.
    cut = orbit_request(split_enabled=True, max_waypoints_per_flight=10)
    parts = planning_adapter.consume_result(cut, planning_adapter.plan(cut))
    assert len(parts) > 1 and all(len(part['waypoints']) <= 10 for part in parts)
    assert all(part['gimbal_pitches'] == [-35] * len(part['waypoints']) for part in parts)


def test_forged_ring_altitudes_are_rejected():
    planned = ring_request()
    for altitude in (0, -5, 501, float('nan'), None):
        result = planning_adapter.plan(planned)
        result['route']['waypoints'][4]['altitude_m'] = altitude
        try:
            planning_adapter.consume_result(planned, result)
        except ValueError:
            pass
        else:
            raise AssertionError('altitude %r accepted' % altitude)


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


def _nudged(value, relative):
    """Every float moved by a relative amount, like a plan made on another OS."""
    if isinstance(value, dict):
        return {key: _nudged(item, relative) for key, item in value.items()}
    if isinstance(value, list):
        return [_nudged(item, relative) for item in value]
    if isinstance(value, float):
        return value - value * relative       # toward zero, so ranges still hold
    return value


def test_saved_results_require_exact_numeric_provenance():
    planned = orbit_request()
    result = planning_adapter.plan(planned)
    assert not planning_adapter.results_match(result, _nudged(result, 1e-12))
    assert not planning_adapter.results_match(result, _nudged(result, 1e-6))
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
    supported, flights = planning_adapter.validate_mission_provenance(mission)
    assert supported and flights[0]['headings']
    moved = deepcopy(mission)
    moved['planning_result']['route']['waypoints'][0]['position']['longitude_deg'] += 1e-7
    moved['waypoints'] = [
        [row['position']['latitude_deg'], row['position']['longitude_deg']]
        for row in moved['planning_result']['route']['waypoints']]
    try:
        planning_adapter.validate_mission_provenance(moved)
    except ValueError as exc:
        assert 'does not match' in str(exc)
    else:
        raise AssertionError('a moved waypoint must not verify')
    # Text, counts and flags still have to match exactly.
    assert not planning_adapter.results_match(result, {**result, 'mapping_style': 'orbit2'})
    assert not planning_adapter.results_match({'count': 3}, {'count': 3.0000000001})
    assert not planning_adapter.results_match({'flag': True}, {'flag': 1})


@pytest.mark.parametrize('contract_version', [1, 2])
def test_orbit_provenance_verifies_result_and_settings(contract_version):
    planned = orbit_request()
    planned['contract_version'] = contract_version
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
    preferred = deepcopy(saved)
    preferred['settings'].update(split_enabled=True, split_count=1)
    assert planning_adapter.validate_mission_provenance(preferred)[0]
    preferred['settings']['split_count'] = 2
    try:
        planning_adapter.validate_mission_provenance(preferred)
    except ValueError:
        pass
    else:
        raise AssertionError('unsplit request accepted with two requested flights')
    with patch.object(planning_adapter, 'SUPPORTED_ENGINE_VERSIONS',
                      planning_adapter.SUPPORTED_ENGINE_VERSIONS + ('1.10.0',)):
        for version in ('1.2.0', '1.10.0', '1.1.0', '9.0.0', '1.2.x'):
            changed = deepcopy(saved)
            changed['planning_result']['engine_version'] = version
            if contract_version == 1 and version in ('1.2.0', '1.10.0'):
                supported, flights = planning_adapter.validate_mission_provenance(changed)
                assert supported and flights[0]['headings']
                assert changed['planning_result']['engine_version'] == version
                changed['planning_result']['route']['waypoints'][3]['heading_deg'] += 1
            try:
                planning_adapter.validate_mission_provenance(changed)
            except ValueError:
                pass
            else:
                raise AssertionError('invalid Orbit provenance accepted')
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



def test_multi_ring_split_provenance_verifies_settings_and_flights():
    planned = ring_request(split_enabled=True, requested_flights=3)
    result = planning_adapter.plan(planned)
    rings = deepcopy(planned['rings'])
    mission = {
        'drone_model': 'mini4pro', 'polygon': [[51.0, -114.0]],
        'planning_request': planned, 'planning_result': result,
        'waypoints': [[row['position']['latitude_deg'], row['position']['longitude_deg']]
                      for row in result['route']['waypoints']],
        'settings': {
            'mapping_style': 'orbit', 'orbit_rings': rings,
            'orbit_radius': rings[0]['radius_m'], 'altitude': rings[0]['altitude_m'],
            'orbit_tilt': rings[0]['gimbal_pitch_deg'], 'speed': 3,
            'reverse_route': False, 'finish_action': 'goHome',
            'capture_mode': 'full', 'side_overlap': 90,
            'split_enabled': True, 'split_count': 3, 'split_max_wp': 200,
        },
    }
    supported, flights = planning_adapter.validate_mission_provenance(mission)
    assert supported and len(flights) == 3
    assert all(flight['headings'] and flight['heights'] and flight['gimbal_pitches']
               for flight in flights)
    old_label = deepcopy(mission)
    old_label['planning_result']['engine_version'] = '1.2.0'
    try:
        planning_adapter.validate_mission_provenance(old_label)
    except ValueError:
        pass
    else:
        raise AssertionError('multi-ring result accepted with an engine 1.2.0 label')
    for change in ('ring', 'split', 'boundary'):
        bad = deepcopy(mission)
        if change == 'ring':
            bad['settings']['orbit_rings'][1]['altitude_m'] += 1
        elif change == 'split':
            bad['settings']['split_count'] = 2
        else:
            bad['planning_result']['flights'][1]['route_start_waypoint_index'] += 1
        try:
            planning_adapter.validate_mission_provenance(bad)
        except ValueError:
            pass
        else:
            raise AssertionError('tampered %s provenance accepted' % change)


def test_orbit_results_follow_the_supported_engine_versions():
    planned = orbit_request()
    planned["contract_version"] = 1
    result = planning_adapter.plan(planned)
    malformed = ('1.2', '1.2.0.1', '1.2.x', '1.2.0-rc1', '+1.2.0', ' 1.2.0', '')
    supported = planning_adapter.SUPPORTED_ENGINE_VERSIONS + ('1.10.0',) + malformed
    with patch.object(planning_adapter, 'SUPPORTED_ENGINE_VERSIONS', supported):
        for version in ('1.2.0', '1.10.0', '1.1.0', '9.0.0', None, 120) + malformed:
            saved = dict(result, engine_version=version)
            for require_supported in (True, False):
                if version in ('1.2.0', '1.10.0'):
                    flights = planning_adapter.consume_result(
                        planned, saved, require_supported=require_supported)
                    assert flights[0]['headings'] == [
                        row['heading_deg'] for row in result['route']['waypoints']]
                else:
                    try:
                        planning_adapter.consume_result(
                            planned, saved, require_supported=require_supported)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('invalid Orbit version accepted: %r' % (version,))


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_') and callable(value)]
    for test in tests:
        test()
    print('%d planning adapter checks passed' % len(tests))


def test_canonical_contract_rejects_legacy_engine_label():
    planned = orbit_request()
    result = dict(planning_adapter.plan(planned), engine_version='1.4.1')
    for require_supported in (True, False):
        with pytest.raises(ValueError, match='does not support the planning contract'):
            planning_adapter.consume_result(planned, result, require_supported=require_supported)


def panorama_request(**overrides):
    values = dict(points=[(-114.09, 51.05)], drone_profile_id='mini4pro', altitude_m=60,
                  speed_m_s=2, overlap_ratio=.3, include_nadir=True, finish_action='Return to Home')
    values.update(overrides)
    return planning_adapter.build_panorama_request(**values)


def test_panorama_flight_carries_outward_headings_and_row_tilts():
    planned = panorama_request()
    assert planned['capture'] == {'mode': 'full_auto', 'overlap_ratio': .3}
    result = planning_adapter.plan(planned)
    assert result['engine_version'] == '2.1.0' and result['contract_version'] == 2
    [flight] = planning_adapter.consume_result(planned, result)
    assert len(flight['waypoints']) == len(flight['headings']) == len(flight['gimbal_pitches']) == 24
    assert flight['headings'][:3] == [0.0, 45.0, 90.0] and flight['heights'] is None
    assert flight['gimbal_pitches'][0] == 0.0 and flight['gimbal_pitches'][-1] == -90.0
    assert sum(action['type'] == 'take_photo' for action in flight['actions']) == 24


def test_panorama_results_need_a_panorama_engine():
    planned = panorama_request()
    result = planning_adapter.plan(planned)
    for changed in (dict(result, engine_version='2.0.0'), dict(result, operation='plan_orbit')):
        with pytest.raises(ValueError):
            planning_adapter.consume_result(planned, changed)
