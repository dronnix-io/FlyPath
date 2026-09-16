"""Thin QGIS-plugin adapter for the versioned shared planning contract."""

import math

from .flypath_engine import __version__ as ENGINE_VERSION
from .flypath_engine.planning import (
    CONTRACT_VERSION, DIRECTION_CONVENTION, PlanningError, plan_2d,
)
from .flypath_engine.profiles import PROFILE_VERSION


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
                  cross_hatch=False, locations=None):
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
        'reverse_route': False,
        'terrain_follow': False,
    }
    if locations is not None:
        request['locations'] = locations
    return request


def plan(request):
    """Run the bundled engine and validate the adapter-facing result shape."""
    result = plan_2d(request)
    consume_result(request, result)
    return result


def consume_result(request, result, *, require_supported=True,
                   legacy_waypoints=None):
    """Return ordered flight dictionaries after checking saved-result integrity."""
    if not isinstance(result, dict) or not isinstance(request, dict):
        raise ValueError('The saved planning request or result is missing.')
    if require_supported and (request.get('contract_version') != CONTRACT_VERSION
                              or result.get('contract_version') != CONTRACT_VERSION):
        raise ValueError('This saved planning contract version is not supported.')
    if require_supported and result.get('engine_version') != ENGINE_VERSION:
        raise ValueError('This saved engine version is not supported.')
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
    flights = result.get('flights')
    if not isinstance(flights, list) or not flights:
        raise ValueError('The saved planning result has no flights.')
    consumed = []
    previous_end = None
    for index, flight in enumerate(flights):
        if not isinstance(flight, dict) or flight.get('index') != index:
            raise ValueError('The saved flight list is invalid.')
        start = flight.get('route_start_waypoint_index')
        end = flight.get('route_end_waypoint_index')
        if (type(start) is not int or type(end) is not int or not 0 <= start <= end < len(points)
                or (previous_end is not None and start != previous_end)):
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
        consumed.append({
            'waypoints': points[start:end + 1],
            'actions': [{**action, 'waypoint_index': action['waypoint_index'] - start}
                        for action in actions],
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
    """Reject a shared result paired with different editable mission inputs."""
    result = mission.get('planning_result')
    if not result:
        return
    request = mission.get('planning_request')
    settings = mission.get('settings') or {}
    if not isinstance(request, dict):
        raise ValueError('The saved planning request is missing.')
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
    consume_result(request, result, require_supported=False,
                   legacy_waypoints=mission.get('waypoints') or None)
    supported = (request.get('contract_version') == CONTRACT_VERSION
                 and result.get('contract_version') == CONTRACT_VERSION
                 and result.get('engine_version') == ENGINE_VERSION)
    if supported and plan_2d(request) != result:
        raise ValueError('The saved planning result does not match its request.')


def _ratio(value):
    return value / 100.0 if type(value) in (int, float) else None


def _finite(value, *, minimum=None, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        return False
    if positive and value <= 0:
        return False
    return minimum is None or value >= minimum
