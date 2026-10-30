"""Thin QGIS-plugin adapter for the versioned shared planning contract."""

import math
import re

from .flypath_engine.planning import (
    DIRECTION_CONVENTION, PlanningError as _PlanningError, plan_2d,
)
from .flypath_engine.precision import CANONICAL_CONTRACT_VERSION as CONTRACT_VERSION
from .flypath_engine.oblique import plan_oblique
from .flypath_engine.orbit import plan_orbit
from .flypath_engine.panorama import AUTO_SPACING_PER_ALTITUDE, plan_panorama
from .flypath_engine.profiles import PROFILE_VERSION


PlanningError = _PlanningError
# Saved Fast oblique results use their original engine geometry during validation.
SUPPORTED_ENGINE_VERSIONS = ('0.4.0', '1.0.0', '1.1.0', '1.2.0', '1.3.0', '1.4.0', '1.4.1', '2.0.0',
                             '2.1.0')
CONTRACT_2_ENGINE_VERSIONS = ('2.0.0', '2.1.0')   # the releases that plan contract 2
SUPPORTED_CONTRACT_VERSIONS = (1, CONTRACT_VERSION)
ORBIT_MIN_ENGINE_VERSION = (1, 2, 0)   # the first engine release with plan_orbit
ORBIT_RINGS_MIN_ENGINE_VERSION = (1, 3, 0)
OBLIQUE_MIN_ENGINE_VERSION = (1, 4, 0)   # the first engine release with plan_oblique
PANORAMA_MIN_ENGINE_VERSION = (2, 1, 0)  # the first engine release with plan_panorama
# Area panoramas: the engine's default point spacing, as a multiple of the altitude.
PANORAMA_AUTO_SPACING_PER_ALTITUDE = AUTO_SPACING_PER_ALTITUDE


def _orbit_capable(version, minimum=ORBIT_MIN_ENGINE_VERSION):
    """True for an explicitly supported Orbit release at or above minimum."""
    return (isinstance(version, str)
            and version in SUPPORTED_ENGINE_VERSIONS
            and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) is not None
            and tuple(int(part) for part in version.split('.')) >= minimum)


FINISH_ACTIONS = {
    'Return to Home': 'return_to_home',
    'Hover in place': 'hover',
    'Land at last waypoint': 'land',
}
WEBSITE_FINISH_ACTIONS = {
    'goHome': 'return_to_home', 'noAction': 'hover', 'autoLand': 'land',
}


def build_request(*, survey_area, drone_profile_id, altitude_m, speed_m_s,
                  side_overlap_ratio, margin_m, automatic_direction,
                  direction_deg, capture_mode, front_overlap_ratio,
                  turn_style, finish_action, split_enabled,
                  requested_flights, max_waypoints_per_flight,
                  cross_hatch=False, reverse_route=False, locations=None):
    """Translate current plugin controls into one engine request."""
    capture = {'mode': 'full_auto' if capture_mode == 'full' else 'semi_auto'}
    if capture['mode'] == 'full_auto':
        capture['front_overlap_ratio'] = front_overlap_ratio
    request = {
        'contract_version': CONTRACT_VERSION,
        'operation': 'plan_2d',
        'mapping_style': '2d',
        'survey_area': survey_area,
        'drone_profile_id': drone_profile_id,
        'profile_version': PROFILE_VERSION,
        'altitude_m': altitude_m,
        'speed_m_s': speed_m_s,
        'side_overlap_ratio': side_overlap_ratio,
        'margin_m': margin_m,
        'direction': {
            'mode': 'automatic' if automatic_direction else 'manual',
            'convention': DIRECTION_CONVENTION,
            'value_deg': direction_deg,
        },
        'capture': capture,
        'turn_style': turn_style,
        'finish_action': FINISH_ACTIONS[finish_action],
        'split': {
            'enabled': bool(split_enabled),
            'requested_flights': requested_flights,
            'max_waypoints_per_flight': max_waypoints_per_flight,
        },
        'cross_hatch': bool(cross_hatch),
        'reverse_route': bool(reverse_route),
        'terrain_follow': False,
    }
    if locations is not None:
        request['locations'] = locations
    return request


def build_orbit_request(*, centre, drone_profile_id, clockwise, speed_m_s, capture_mode,
                        side_overlap_ratio, finish_action, max_waypoints_per_flight,
                        radius_m=None, altitude_m=None, gimbal_pitch_deg=None, rings=None,
                        split_enabled=False, requested_flights=1):
    """Translate the orbit controls into one engine request.

    `centre` is (lon, lat) in WGS84. Give one ring as radius, altitude and pitch,
    or `rings` as (radius_m, altitude_m, gimbal_pitch_deg) tuples flown in order.
    A single ring always uses the single-ring request, so its result stays the
    one flypath.io and earlier plugins verify. Full auto sets the photo spacing
    from the side overlap; semi auto follows speed and the photo interval.
    Splitting divides the rings into flights (engine 1.3.0 and newer)."""
    capture = {'mode': 'full_auto' if capture_mode == 'full' else 'semi_auto'}
    if capture['mode'] == 'full_auto':
        capture['side_overlap_ratio'] = side_overlap_ratio
    if rings is not None and len(rings) == 1:
        (radius_m, altitude_m, gimbal_pitch_deg), rings = rings[0], None
    shape = ({'rings': [{'radius_m': radius, 'altitude_m': altitude, 'gimbal_pitch_deg': pitch}
                        for radius, altitude, pitch in rings]}
             if rings is not None else
             {'radius_m': radius_m, 'altitude_m': altitude_m, 'gimbal_pitch_deg': gimbal_pitch_deg})
    return {
        'contract_version': CONTRACT_VERSION,
        'operation': 'plan_orbit',
        'mapping_style': 'orbit',
        'drone_profile_id': drone_profile_id,
        'profile_version': PROFILE_VERSION,
        'centre': {'latitude_deg': centre[1], 'longitude_deg': centre[0]},
        **shape,
        'direction': 'clockwise' if clockwise else 'counterclockwise',
        'speed_m_s': speed_m_s,
        'capture': capture,
        'finish_action': FINISH_ACTIONS[finish_action],
        'split': ({'enabled': True, 'requested_flights': requested_flights,
                   'max_waypoints_per_flight': max_waypoints_per_flight} if split_enabled else
                  {'enabled': False, 'max_waypoints_per_flight': max_waypoints_per_flight}),
        'terrain_follow': False,
    }


def build_oblique_request(*, gimbal_pitch_deg, include_nadir, pattern='fast', **controls):
    """Translate the oblique controls into one engine request.

    The survey, overlap, direction, capture, path and split controls are the 2D
    ones; the pattern, camera pitch and optional nadir pass are oblique's own.
    'full' flies four oblique passes, one facing each side; 'fast' flies two
    crossing grids facing along their lines. The nadir pass comes first when
    included. Reverse route flies each pass from its other end. Cross-hatch
    and per-flight locations do not apply."""
    request = build_request(**controls)
    request.pop('cross_hatch')
    locations = request.get('locations')
    if isinstance(locations, dict) and 'per_flight' in locations:
        request.pop('locations')
    request.update(operation='plan_oblique', mapping_style='oblique',
                   gimbal_pitch_deg=gimbal_pitch_deg, include_nadir=bool(include_nadir),
                   pattern=pattern)
    return request


def build_panorama_request(*, drone_profile_id, altitude_m, speed_m_s, overlap_ratio,
                           include_nadir, finish_action, max_waypoints_per_flight=200,
                           points=None, survey_area=None, point_spacing_m=None):
    """Translate the panorama controls into one engine request.

    Give `points` as (lon, lat) in WGS84, flown in order, or a `survey_area`
    for the engine to place points over, `point_spacing_m` apart (None for
    its default, the altitude). Every photo has its own waypoint,
    so the capture mode is always full automatic; the overlap applies around
    each camera row and between rows. Flights are cut between panoramas to
    fit the waypoint limit and one battery."""
    if survey_area is not None:
        place = {'survey_area': survey_area}
        if point_spacing_m is not None:
            place['point_spacing_m'] = point_spacing_m
    else:
        place = {'points': [{'latitude_deg': lat, 'longitude_deg': lon} for lon, lat in points]}
    return {
        'contract_version': CONTRACT_VERSION,
        'operation': 'plan_panorama',
        'mapping_style': 'panorama',
        'drone_profile_id': drone_profile_id,
        'profile_version': PROFILE_VERSION,
        **place,
        'altitude_m': altitude_m,
        'speed_m_s': speed_m_s,
        'capture': {'mode': 'full_auto', 'overlap_ratio': overlap_ratio},
        'include_nadir': bool(include_nadir),
        'finish_action': FINISH_ACTIONS[finish_action],
        'split': {'max_waypoints_per_flight': max_waypoints_per_flight},
        'terrain_follow': False,
    }


def plan(request, *, engine_version=None):
    """Run the bundled engine; consumers validate the returned result once."""
    if request.get('operation') == 'plan_orbit':
        return plan_orbit(request)
    if request.get('operation') == 'plan_panorama':
        return plan_panorama(request)
    if request.get('operation') == 'plan_oblique':
        return plan_oblique(request, engine_version=engine_version)
    return plan_2d(request)


def needs_geometry_update(result):
    return (result.get('operation') == 'plan_oblique'
            and result.get('engine_version') == '1.4.0'
            and result.get('oblique', {}).get('pattern') == 'fast')


def consume_result(request, result, *, require_supported=True,
                   legacy_waypoints=None):
    """Return ordered flight dictionaries after checking saved-result integrity."""
    if not isinstance(result, dict) or not isinstance(request, dict):
        raise ValueError('The saved planning request or result is missing.')
    if request.get('contract_version') == 2 and result.get('engine_version') not in CONTRACT_2_ENGINE_VERSIONS:
        raise ValueError('This saved engine version does not support the planning contract.')
    if require_supported and (request.get('contract_version') not in SUPPORTED_CONTRACT_VERSIONS
                              or result.get('contract_version') != request.get('contract_version')):
        raise ValueError('This saved planning contract version is not supported.')
    if require_supported and result.get('engine_version') not in SUPPORTED_ENGINE_VERSIONS:
        raise ValueError('This saved engine version is not supported.')
    if request.get('operation') == 'plan_orbit' and (
            result.get('operation') != 'plan_orbit'
            or not _orbit_capable(result.get('engine_version'))):
        raise ValueError('This saved orbit engine version is not supported.')
    oblique = request.get('operation') == 'plan_oblique'
    if oblique and (result.get('operation') != 'plan_oblique'
                    or not _orbit_capable(result.get('engine_version'), OBLIQUE_MIN_ENGINE_VERSION)):
        raise ValueError('This saved oblique engine version is not supported.')
    panorama = request.get('operation') == 'plan_panorama'
    if panorama and (result.get('operation') != 'plan_panorama'
                     or not _orbit_capable(result.get('engine_version'), PANORAMA_MIN_ENGINE_VERSION)):
        raise ValueError('This saved panorama engine version is not supported.')
    if any(result.get(key) != request.get(key)
           for key in ('profile_version', 'drone_profile_id')):
        raise ValueError('The saved planning result does not match its request.')
    capture = result.get('capture')
    statistics = result.get('statistics')
    validation = result.get('validation')
    if (not isinstance(capture, dict)
            or capture.get('mode') != request.get('capture', {}).get('mode')
            or not _finite(capture.get('profile_interval_s'), positive=True)
            or not _finite(capture.get('shot_spacing_m'), positive=True)):
        raise ValueError('The saved capture result is invalid.')
    numeric_stats = ('survey_area_m2', 'route_distance_m', 'known_distance_m',
                     'known_estimated_seconds')
    integer_stats = ('photo_count', 'strip_count', 'waypoint_count', 'battery_count')
    if (not isinstance(statistics, dict)
            or any(not _finite(statistics.get(key), minimum=0) for key in numeric_stats)
            or any(type(statistics.get(key)) is not int or statistics[key] < 0
                   for key in integer_stats)
            or type(statistics.get('estimates_complete')) is not bool
            or statistics.get('photo_count_kind') not in ('actions', 'estimate')):
        raise ValueError('The saved planning statistics are invalid.')
    errors = validation.get('errors') if isinstance(validation, dict) else None
    if (type(validation.get('export_allowed')) is not bool
            if isinstance(validation, dict) else True) or not isinstance(errors, list) or any(
                not isinstance(error, dict) or not isinstance(error.get('code'), str)
                or not isinstance(error.get('message'), str) for error in errors):
        raise ValueError('The saved export validation is invalid.')
    route = result.get('route')
    rows = route.get('waypoints') if isinstance(route, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError('The saved planning result has no route.')
    points = []
    for index, row in enumerate(rows):
        position = row.get('position') if isinstance(row, dict) else None
        if (not isinstance(row, dict) or row.get('index') != index
                or not isinstance(position, dict)
                or type(position.get('latitude_deg')) not in (int, float)
                or type(position.get('longitude_deg')) not in (int, float)
                or not math.isfinite(position['latitude_deg'])
                or not -90 <= position['latitude_deg'] <= 90
                or not math.isfinite(position['longitude_deg'])
                or not -180 <= position['longitude_deg'] <= 180):
            raise ValueError('The saved planning route is invalid.')
        points.append((float(position['longitude_deg']),
                       float(position['latitude_deg'])))
    if legacy_waypoints:
        expected = [(float(lon), float(lat)) for lat, lon in legacy_waypoints]
        if len(expected) != len(points) or any(
                abs(a - b) > 1e-9 for pair, saved in zip(points, expected)
                for a, b in zip(pair, saved)):
            raise ValueError('The saved route does not match the planning result.')
    # Orbit routes carry a heading per waypoint (facing the centre); 2D does not.
    # Oblique passes hold one heading each, and its nadir pass has none.
    raw_headings = [row.get('heading_deg') for row in rows]
    headings = None
    if any(value is not None for value in raw_headings):
        if any((value is not None or not oblique)
               and (not _finite(value) or not -180 <= value <= 180) for value in raw_headings):
            raise ValueError('The saved route headings are invalid.')
        headings = [None if value is None else float(value) for value in raw_headings]
    # Multi-ring orbits also give each waypoint its own altitude.
    raw_altitudes = [row.get('altitude_m') for row in rows]
    altitudes = None
    if any(value is not None for value in raw_altitudes):
        if any(not _finite(value, positive=True) or value > 500 for value in raw_altitudes):
            raise ValueError('The saved route altitudes are invalid.')
        altitudes = [float(value) for value in raw_altitudes]
    flights = result.get('flights')
    if not isinstance(flights, list) or not flights:
        raise ValueError('The saved planning result has no flights.')
    # Adjacent flights share their seam waypoint, except orbit flights that
    # split between rings, oblique flights between passes and panorama flights
    # between points: the next one starts at its ring's, pass's or point's
    # first waypoint.
    orbit = request.get('operation') == 'plan_orbit'
    consumed = []
    previous_end = None
    for index, flight in enumerate(flights):
        if not isinstance(flight, dict) or flight.get('index') != index:
            raise ValueError('The saved flight list is invalid.')
        start = flight.get('route_start_waypoint_index')
        end = flight.get('route_end_waypoint_index')
        if (type(start) is not int or type(end) is not int or not 0 <= start <= end < len(points)
                or (previous_end is not None and start != previous_end
                    and not (start == previous_end + 1
                             and ((orbit and 'ring_start' in (rows[start].get('roles') or ()))
                                  or (oblique and 'pass_start' in (rows[start].get('roles') or ()))
                                  or (panorama and 'point_start' in (rows[start].get('roles') or ())))))):
            raise ValueError('The saved flight boundaries are invalid.')
        actions = flight.get('actions')
        if not isinstance(actions, list) or any(
                not isinstance(action, dict)
                or action.get('type') not in ('rotate_camera', 'hover', 'take_photo')
                or type(action.get('waypoint_index')) is not int
                or not start <= action['waypoint_index'] <= end
                or (action['type'] == 'hover'
                    and (type(action.get('duration_s')) not in (int, float)
                         or not math.isfinite(action['duration_s'])
                         or action['duration_s'] < 0))
                or (action['type'] == 'rotate_camera'
                    and (not _finite(action.get('pitch_deg'))
                         or not -180 <= action['pitch_deg'] <= 180))
                for action in actions):
            raise ValueError('The saved flight actions are invalid.')
        flight_headings = headings[start:end + 1] if headings else None
        if flight_headings and all(value is None for value in flight_headings):
            flight_headings = None          # an oblique nadir pass faces along its lines
        consumed.append({
            'waypoints': points[start:end + 1],
            'actions': [{**action, 'waypoint_index': action['waypoint_index'] - start}
                        for action in actions],
            'headings': flight_headings,
            'heights': altitudes[start:end + 1] if altitudes else None,
            'gimbal_pitches': (_pitch_per_waypoint(actions, start, end)
                               if altitudes or oblique or panorama else None),
            'flight': flight,
        })
        previous_end = end
    if consumed[0]['flight']['route_start_waypoint_index'] != 0 or previous_end != len(points) - 1:
        raise ValueError('The saved flights do not cover the route.')
    strips = route.get('strips')
    photo_actions = sum(action['type'] == 'take_photo'
                        for flight in consumed for action in flight['actions'])
    if (not isinstance(strips, list)
            or statistics['waypoint_count'] != len(points)
            or statistics['battery_count'] != len(consumed)
            or statistics['strip_count'] != len(strips)
            or (capture['mode'] == 'full_auto'
                and statistics['photo_count'] != photo_actions)):
        raise ValueError('The saved planning statistics do not match its route.')
    return consumed


def validate_mission_provenance(mission):
    """Validate a saved plan once and return its compatibility and flights."""
    result = mission.get('planning_result')
    if not result:
        return None
    request = mission.get('planning_request')
    settings = mission.get('settings') or {}
    oblique = settings.get('mapping_style') == 'oblique'
    if not isinstance(request, dict):
        raise ValueError('The saved planning request is missing.')
    if oblique and not _orbit_capable(result.get('engine_version'), OBLIQUE_MIN_ENGINE_VERSION):
        raise ValueError('This saved oblique engine version is not supported.')
    if settings.get('mapping_style') == 'orbit':
        if not _orbit_capable(result.get('engine_version')):
            raise ValueError('This saved orbit engine version is not supported.')
        rings = settings.get('orbit_rings') or [{
            'radius_m': settings.get('orbit_radius'),
            'altitude_m': settings.get('altitude'),
            'gimbal_pitch_deg': settings.get('orbit_tilt'),
        }]
        request_split = request.get('split')
        if (len(rings) > 1 or (isinstance(request_split, dict)
                              and request_split.get('enabled') is True)) and not _orbit_capable(
                result.get('engine_version'), ORBIT_RINGS_MIN_ENGINE_VERSION):
            raise ValueError('This saved orbit needs planning engine 1.3.0 or newer.')
        expected = {
            'operation': 'plan_orbit',
            'mapping_style': 'orbit',
            'drone_profile_id': mission.get('drone_model'),
            'centre': dict(zip(('latitude_deg', 'longitude_deg'),
                               (mission.get('polygon') or [[None, None]])[0])),
            'speed_m_s': settings.get('speed'),
            'direction': 'counterclockwise' if settings.get('reverse_route') else 'clockwise',
            'finish_action': WEBSITE_FINISH_ACTIONS.get(settings.get('finish_action')),
            'terrain_follow': False,
        }
        if any(request.get(key) != value for key, value in expected.items()):
            raise ValueError('The saved orbit request does not match the mission settings.')
        if len(rings) == 1:
            if (request.get('rings') is not None or any(
                    request.get(key) != value for key, value in rings[0].items())):
                raise ValueError('The saved orbit rings do not match the mission settings.')
        elif (request.get('rings') != rings or any(
                key in request for key in ('radius_m', 'altitude_m', 'gimbal_pitch_deg'))):
            raise ValueError('The saved orbit rings do not match the mission settings.')
        capture = request.get('capture')
        full = settings.get('capture_mode') == 'full'
        if (not isinstance(capture, dict)
                or capture.get('mode') != ('full_auto' if full else 'semi_auto')
                or (full and capture.get('side_overlap_ratio') != _ratio(settings.get('side_overlap')))):
            raise ValueError('The saved orbit capture does not match the mission settings.')
        split = request.get('split')
        enabled = settings.get('split_enabled', False)
        request_enabled = split.get('enabled') if isinstance(split, dict) else None
        # The website can save the checked split preference even when an orbit
        # fitting one flight has an unsplit planning request.
        preference_only = (enabled is True and settings.get('split_count', 1) == 1
                           and request_enabled is False)
        if (not isinstance(split, dict) or (request_enabled is not enabled and not preference_only)
                or split.get('max_waypoints_per_flight') != settings.get('split_max_wp')
                or (request_enabled and split.get('requested_flights') != settings.get('split_count'))
                or (not request_enabled and 'requested_flights' in split)):
            raise ValueError('The saved orbit split does not match the mission settings.')
    else:
        expected = {
            'mapping_style': settings.get('mapping_style', '2d'),
            'drone_profile_id': mission.get('drone_model'),
            'altitude_m': settings.get('altitude'),
            'speed_m_s': settings.get('speed'),
            'side_overlap_ratio': _ratio(settings.get('side_overlap')),
            'margin_m': settings.get('margin', 0),
            'turn_style': settings.get('flight_path', 'curved'),
            'finish_action': WEBSITE_FINISH_ACTIONS.get(settings.get('finish_action')),
            'cross_hatch': bool(settings.get('cross_hatch')),
            'reverse_route': bool(settings.get('reverse_route')),
            'terrain_follow': bool(settings.get('terrain_follow')),
        }
        if oblique:
            if settings.get('terrain_follow') or settings.get('cross_hatch'):
                raise ValueError('Oblique website missions do not support terrain or cross-hatch.')
            expected.pop('cross_hatch')
            expected.update(operation='plan_oblique',
                            pattern=settings.get('oblique_pattern'),
                            gimbal_pitch_deg=settings.get('oblique_pitch'),
                            include_nadir=settings.get('oblique_nadir'))
        if any(request.get(key) != value for key, value in expected.items()):
            raise ValueError('The saved planning request does not match the mission settings.')
        capture = request.get('capture')
        expected_mode = 'full_auto' if settings.get('capture_mode') == 'full' else 'semi_auto'
        if not isinstance(capture, dict) or capture.get('mode') != expected_mode:
            raise ValueError('The saved capture request does not match the mission settings.')
        if expected_mode == 'full_auto' and capture.get('front_overlap_ratio') != _ratio(settings.get('front_overlap')):
            raise ValueError('The saved overlap request does not match the mission settings.')
        split = request.get('split')
        enabled = settings.get('split_enabled')
        if (not isinstance(split, dict) or split.get('enabled') is not enabled
                or split.get('max_waypoints_per_flight') != settings.get('split_max_wp')
                or (enabled and split.get('requested_flights') != settings.get('split_count'))
                or (not enabled and split.get('requested_flights') != 1)):
            raise ValueError('The saved split request does not match the mission settings.')
        direction = request.get('direction')
        automatic = bool(settings.get('auto_direction'))
        if (not isinstance(direction, dict)
                or direction.get('mode') != ('automatic' if automatic else 'manual')
                or (not automatic and direction.get('value_deg') != settings.get('direction'))):
            raise ValueError('The saved direction request does not match the mission settings.')
        area = request.get('survey_area')
        exterior = area.get('exterior') if isinstance(area, dict) else None
        polygon = mission.get('polygon') or []
        request_polygon = ([[point.get('latitude_deg'), point.get('longitude_deg')]
                            for point in exterior] if isinstance(exterior, list) else None)
        if request_polygon != polygon:
            raise ValueError('The saved planning area does not match the mission geometry.')
    flights = consume_result(request, result, require_supported=False,
                             legacy_waypoints=mission.get('waypoints') or None)
    if settings.get('mapping_style') == 'orbit' and any(
            flight['headings'] is None for flight in flights):
        raise ValueError('The saved orbit route is invalid.')
    supported = (request.get('contract_version') in SUPPORTED_CONTRACT_VERSIONS
                 and result.get('contract_version') == request.get('contract_version')
                 and result.get('engine_version') in SUPPORTED_ENGINE_VERSIONS)
    replay_version = result['engine_version'] if oblique else None
    if supported and not results_match(
            {**plan(request, engine_version=replay_version), 'engine_version': result['engine_version']}, result):
        kind = 'orbit' if settings.get('mapping_style') == 'orbit' else 'planning'
        raise ValueError('The saved %s result does not match its request.' % kind)
    return supported, flights


def results_match(first, second):
    """Compare saved provenance exactly, without treating flags as numbers."""
    if isinstance(first, dict) and isinstance(second, dict):
        return first.keys() == second.keys() and all(
            results_match(first[key], second[key]) for key in first)
    if isinstance(first, list) and isinstance(second, list):
        return len(first) == len(second) and all(
            results_match(a, b) for a, b in zip(first, second))
    numbers = (int, float)
    if (isinstance(first, numbers) and isinstance(second, numbers)
            and not isinstance(first, bool) and not isinstance(second, bool)):
        return first == second
    return type(first) is type(second) and first == second


def _pitch_per_waypoint(actions, start, end):
    """The camera pitch in force at each waypoint, from the rotate actions."""
    rotations = {action['waypoint_index']: float(action['pitch_deg'])
                 for action in actions if action['type'] == 'rotate_camera'}
    if start not in rotations:
        raise ValueError('The saved flight does not set the camera at its start.')
    pitches, current = [], None
    for index in range(start, end + 1):
        current = rotations.get(index, current)
        pitches.append(current)
    return pitches


def _ratio(value):
    return value / 100.0 if type(value) in (int, float) else None


def _finite(value, *, minimum=None, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        return False
    if positive and value <= 0:
        return False
    return minimum is None or value >= minimum
