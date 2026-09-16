"""Versioned first-phase 2D planning contract."""

import math

from . import grid
from .measurements import route_distance_m, survey_area_m2
from .profiles import PROFILE_VERSION, drone_profile


CONTRACT_VERSION = 1
ENGINE_VERSION = "0.4.0"
DIRECTION_CONVENTION = "plugin_grid_ccw_from_north"
MAX_WAYPOINT_LIMIT = 200
MAX_ROUTE_WAYPOINTS = 40 * (MAX_WAYPOINT_LIMIT - 1) + 1
MAX_ALTITUDE_M = 500
MAX_MARGIN_M = 1_000


class PlanningError(ValueError):
    """Invalid planning request with a stable, displayable error payload."""

    def __init__(self, code, message, field=None):
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message

    def as_dict(self):
        result = {"code": self.code, "message": self.message}
        if self.field:
            result["field"] = self.field
        return result


def plan_2d(request):
    """Return one deterministic, JSON-compatible 2D planning result."""
    if not isinstance(request, dict):
        raise PlanningError("invalid_request", "Planning request must be an object.")
    _keys(request, {
        "contract_version", "operation", "source_mission_id", "survey_area",
        "drone_profile_id", "profile_version", "altitude_m", "speed_m_s",
        "side_overlap_ratio", "margin_m", "direction", "capture",
        "turn_style", "finish_action", "split", "locations", "cross_hatch",
        "reverse_route", "terrain_follow", "mapping_style",
    }, "request")
    if request.get("mapping_style", "2d") != "2d":
        raise PlanningError("unsupported_feature", "Only 2d mapping is supported.", "mapping_style")
    _version(request.get("contract_version"), CONTRACT_VERSION, "contract_version")
    if request.get("operation", "plan_2d") != "plan_2d":
        raise PlanningError("unsupported_operation", "Only plan_2d is supported.", "operation")
    cross_hatch = _optional_bool(request.get("cross_hatch", False), "cross_hatch")
    reverse_route = _optional_bool(request.get("reverse_route", False), "reverse_route")
    if request.get("terrain_follow") not in (None, False):
        raise PlanningError("unsupported_feature", "terrain_follow is outside the first-phase contract.", "terrain_follow")
    _version(request.get("profile_version"), PROFILE_VERSION, "profile_version")

    profile_id = request.get("drone_profile_id")
    try:
        profile_name, profile = drone_profile(profile_id)
    except (KeyError, TypeError):
        raise PlanningError("profile_not_found", "Drone profile is not available.", "drone_profile_id") from None

    altitude = _number(request.get("altitude_m"), "altitude_m", minimum=0, maximum=MAX_ALTITUDE_M, exclusive=True)
    speed = _number(request.get("speed_m_s"), "speed_m_s", minimum=0, exclusive=True)
    side_overlap = _number(request.get("side_overlap_ratio"), "side_overlap_ratio", minimum=0, maximum=1, maximum_exclusive=True)
    margin = _number(request.get("margin_m", 0), "margin_m", minimum=0, maximum=MAX_MARGIN_M)
    aircraft, camera = profile["aircraft"], profile["camera"]
    if not aircraft["min_speed_ms"] <= speed <= aircraft["max_speed_ms"]:
        raise PlanningError("speed_out_of_profile_range", "Speed is outside the drone profile range.", "speed_m_s")

    capture = request.get("capture")
    if not isinstance(capture, dict) or capture.get("mode") not in ("semi_auto", "full_auto"):
        raise PlanningError("invalid_capture_mode", "Capture mode must be semi_auto or full_auto.", "capture.mode")
    _keys(capture, {"mode", "interval_s", "front_overlap_ratio"}, "capture")
    capture_mode = capture["mode"]
    interval = _number(camera.get("min_shoot_interval_s"), "drone_profile.camera.min_shoot_interval_s", minimum=0, exclusive=True)
    if "interval_s" in capture and _number(capture["interval_s"], "capture.interval_s", minimum=0, exclusive=True) != interval:
        raise PlanningError("profile_interval_override", "Capture interval is owned by the drone profile.", "capture.interval_s")
    front_overlap = None
    if capture_mode == "full_auto":
        front_overlap = _number(capture.get("front_overlap_ratio"), "capture.front_overlap_ratio", minimum=0, maximum=1, maximum_exclusive=True)

    split = request.get("split")
    if not isinstance(split, dict) or type(split.get("enabled")) is not bool:
        raise PlanningError("invalid_split", "split.enabled must be a boolean.", "split.enabled")
    _keys(split, {"enabled", "requested_flights", "max_waypoints_per_flight"}, "split")
    requested_flights = _integer(split.get("requested_flights", 1), "split.requested_flights", minimum=1)
    waypoint_limit = _integer(split.get("max_waypoints_per_flight"), "split.max_waypoints_per_flight", minimum=2, maximum=MAX_WAYPOINT_LIMIT)
    finish_action = request.get("finish_action", "return_to_home")
    if finish_action not in ("return_to_home", "return_to_first_waypoint", "hover", "land"):
        raise PlanningError("invalid_finish_action", "Finish action is not supported.", "finish_action")
    turn_style = request.get("turn_style", "curved")
    if turn_style not in ("curved", "straight"):
        raise PlanningError("invalid_turn_style", "Turn style must be curved or straight.", "turn_style")
    locations = _locations(request.get("locations"), requested_flights)
    _validate_area(request.get("survey_area"))

    direction = request.get("direction")
    if not isinstance(direction, dict) or direction.get("mode") not in ("manual", "automatic"):
        raise PlanningError("invalid_direction", "Direction mode must be manual or automatic.", "direction.mode")
    _keys(direction, {"mode", "convention", "value_deg"}, "direction")
    if direction.get("convention", DIRECTION_CONVENTION) != DIRECTION_CONVENTION:
        raise PlanningError("unsupported_direction_convention", "Direction convention is not supported.", "direction.convention")

    survey_area = request.get("survey_area")
    footprint_across = altitude * _number(camera.get("sensor_width_mm"), "drone_profile.camera.sensor_width_mm", minimum=0, exclusive=True) / _number(camera.get("focal_length_mm"), "drone_profile.camera.focal_length_mm", minimum=0, exclusive=True)
    line_spacing = max(footprint_across * (1 - side_overlap), .5)
    if direction["mode"] == "manual":
        resolved_direction = _number(direction.get("value_deg"), "direction.value_deg") % 180
    else:
        try:
            resolved_direction = grid.find_optimal_direction(
                survey_area, line_spacing, max_scan_steps=MAX_ROUTE_WAYPOINTS
            )
        except grid.GridCapacityError as exc:
            raise PlanningError("capacity_exceeded", str(exc), "survey_area") from None
        except ValueError as exc:
            raise PlanningError("invalid_geometry", str(exc), "survey_area") from None

    if capture_mode == "full_auto":
        footprint_along = altitude * _number(camera.get("sensor_height_mm"), "drone_profile.camera.sensor_height_mm", minimum=0, exclusive=True) / camera["focal_length_mm"]
        shot_spacing = max(footprint_along * (1 - front_overlap), .5)
        densify_spacing = shot_spacing
    else:
        shot_spacing = max(speed * interval, .5)
        densify_spacing = None

    try:
        generated = grid.generate_grid(
            survey_area, altitude_m=altitude, shot_spacing_m=shot_spacing,
            side_overlap=side_overlap, direction_deg=resolved_direction,
            margin_m=margin, camera=camera, densify_spacing=densify_spacing,
            include_metadata=True, max_waypoints=MAX_ROUTE_WAYPOINTS,
        )
        if cross_hatch:
            crossing = grid.generate_grid(
                survey_area, altitude_m=altitude, shot_spacing_m=shot_spacing,
                side_overlap=side_overlap, direction_deg=(resolved_direction + 90) % 180,
                margin_m=margin, camera=camera, densify_spacing=densify_spacing,
                include_metadata=True, max_waypoints=MAX_ROUTE_WAYPOINTS,
            )
            offset = len(generated["waypoints"])
            strip_offset = len(generated["strips"])
            generated["waypoints"].extend(crossing["waypoints"])
            generated["strips"].extend({
                **survey_pass,
                "index": strip_offset + index,
                "start_waypoint_index": survey_pass["start_waypoint_index"] + offset,
                "end_waypoint_index": survey_pass["end_waypoint_index"] + offset,
            } for index, survey_pass in enumerate(crossing["strips"]))
            if len(generated["waypoints"]) > MAX_ROUTE_WAYPOINTS:
                raise grid.GridCapacityError("Planned route exceeds the engine waypoint capacity.")
        if reverse_route:
            waypoint_count = len(generated["waypoints"])
            generated["waypoints"].reverse()
            generated["strips"] = [
                {
                    **survey_pass,
                    "index": index,
                    "start_waypoint_index": waypoint_count - 1 - survey_pass["end_waypoint_index"],
                    "end_waypoint_index": waypoint_count - 1 - survey_pass["start_waypoint_index"],
                }
                for index, survey_pass in enumerate(reversed(generated["strips"]))
            ]
        area = survey_area_m2(survey_area)
    except grid.GridCapacityError as exc:
        raise PlanningError("capacity_exceeded", str(exc), "survey_area") from None
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanningError("invalid_geometry", str(exc), "survey_area") from None

    points = generated["waypoints"]
    waypoint_roles, strips = _route_metadata(points, generated["strips"])
    waypoints = [
        {"index": index, "position": point, "roles": sorted(waypoint_roles[index])}
        for index, point in enumerate(points)
    ]

    max_flights = max(1, len(points) - 1)
    if requested_flights > max_flights:
        raise PlanningError("too_many_requested_flights", "requested_flights exceeds the available route edges.", "split.requested_flights")
    per_flight_locations = locations is not None and locations[0] == "per_flight"
    usable_seconds = _number(aircraft.get("battery_safe_min"), "drone_profile.aircraft.battery_safe_min", minimum=0, exclusive=True) * 60
    photo_allowance = max(2.5, interval)
    edges = _edge_distances(points)

    minimum_by_waypoints = math.ceil((len(points) - 1) / (waypoint_limit - 1)) if len(points) > 1 else 1
    minimum = max(requested_flights, minimum_by_waypoints) if split["enabled"] else 1
    chosen = None
    blocking = []
    for flight_count in range(minimum, max_flights + 1):
        if not split["enabled"] and flight_count > 1:
            break
        if per_flight_locations and flight_count != requested_flights:
            break
        candidate = _balanced_boundaries(
            points, edges, flight_count, waypoint_limit, speed, capture_mode,
            photo_allowance, usable_seconds, finish_action, locations,
        )
        if candidate is not None:
            chosen = candidate
            break
    if chosen is None:
        one_waypoint_over = len(points) > waypoint_limit
        one_time = _segment_seconds(edges, 0, len(points) - 1, speed, capture_mode, photo_allowance)
        if not split["enabled"]:
            if one_waypoint_over:
                blocking.append(_issue("splitting_required_waypoint_limit", "Enable splitting to satisfy the waypoint limit."))
            if one_time > usable_seconds:
                blocking.append(_issue("splitting_required_battery_limit", "Enable splitting to satisfy the usable battery-time limit."))
            chosen = [(0, len(points) - 1)]
        elif per_flight_locations:
            blocking.append(_issue("per_flight_locations_need_review", "Splitting requires a different flight count; review per-flight locations before regenerating."))
            chosen = _balanced_boundaries(points, edges, requested_flights, max(len(points), waypoint_limit), speed, capture_mode, photo_allowance, math.inf, finish_action, locations) or [(0, len(points) - 1)]
        else:
            blocking.append(_issue("flight_cannot_fit", "No valid split fits the waypoint and usable battery-time limits."))
            chosen = [(0, len(points) - 1)]

    flights = []
    warnings = []
    for flight_index, (start, end) in enumerate(chosen):
        location = _flight_location(locations, flight_index)
        flight = _flight_result(
            flight_index, start, end, points, edges, speed, capture_mode,
            interval, photo_allowance, usable_seconds, finish_action, location,
        )
        flights.append(flight)
        if not flight["estimates_complete"]:
            warnings.append(_issue("travel_estimate_incomplete", f"Flight {flight_index + 1} is missing launch or recovery location travel."))
        if flight["known_estimated_seconds"] > usable_seconds:
            blocking.append(_issue("battery_time_exceeded", f"Flight {flight_index + 1} exceeds the usable battery-time limit."))
            if not split["enabled"] and not any(
                error["code"] == "splitting_required_battery_limit" for error in blocking
            ):
                blocking.append(_issue(
                    "splitting_required_battery_limit",
                    "Enable splitting to satisfy the usable battery-time limit.",
                ))
        if flight["waypoint_count"] > waypoint_limit:
            blocking.append(_issue("waypoint_limit_exceeded", f"Flight {flight_index + 1} exceeds the waypoint limit."))

    complete = all(flight["estimates_complete"] for flight in flights)
    total_distance = sum(flight["total_distance_m"] for flight in flights) if complete else None
    total_seconds = sum(flight["total_estimated_seconds"] for flight in flights) if complete else None
    result = {
        "contract_version": CONTRACT_VERSION,
        "engine_version": ENGINE_VERSION,
        "profile_version": PROFILE_VERSION,
        "drone_profile_id": profile_id,
        "drone_profile_name": profile_name,
        "resolved_direction": {"convention": DIRECTION_CONVENTION, "value_deg": generated["resolved_direction_deg"]},
        "turn_style": turn_style,
        "finish_action": finish_action,
        "route": {"waypoints": waypoints, "strips": strips},
        "flights": flights,
        "statistics": {
            "survey_area_m2": area,
            "route_distance_m": sum(f["route_distance_m"] for f in flights),
            "outbound_distance_m": _complete_sum(f["outbound_distance_m"] for f in flights),
            "recovery_distance_m": _complete_sum(f["recovery_distance_m"] for f in flights),
            "known_distance_m": sum(f["known_distance_m"] for f in flights),
            "total_distance_m": total_distance,
            "known_estimated_seconds": sum(f["known_estimated_seconds"] for f in flights),
            "total_estimated_seconds": total_seconds,
            "photo_count": sum(f["photo_count"] for f in flights),
            "photo_count_kind": "actions" if capture_mode == "full_auto" else "estimate",
            "strip_count": len(strips),
            "waypoint_count": len(points),
            "battery_count": len(flights),
            "estimates_complete": complete,
        },
        "capture": {"mode": capture_mode, "profile_interval_s": interval, "shot_spacing_m": shot_spacing},
        "assumptions": [
            {"code": "straight_segment_estimate", "message": "Distance and time use straight waypoint segments for every turn style."},
            {"code": "unmodeled_operations", "message": "Takeoff, climb, landing, and additional turn delays are not modeled."},
            {"code": "battery_reserve_separate", "message": "The profile usable-time budget is applied without a blanket time multiplier."},
        ],
        "warnings": warnings,
        "validation": {"export_allowed": not blocking, "errors": blocking},
    }
    _ensure_finite_json(result)
    return result


def _route_metadata(points, strips):
    """Attach roles using scan-line boundaries retained by grid generation."""
    roles = [set() for _ in points]
    for survey_pass in strips:
        start = survey_pass["start_waypoint_index"]
        index = survey_pass["end_waypoint_index"] + 1
        roles[start].add("survey_strip_start")
        roles[index - 1].add("survey_strip_end")
        if start == index - 1:
            roles[start].add("survey_strip_point")
        for waypoint_index in range(start + 1, index - 1):
            roles[waypoint_index].add("survey_strip_interior")
    if roles:
        roles[0].add("route_start")
        roles[-1].add("route_end")
    return roles, strips


def _complete_sum(values):
    values = list(values)
    return sum(values) if all(value is not None for value in values) else None


def _edge_distances(points):
    return [route_distance_m(points[index:index + 2]) for index in range(len(points) - 1)]


def _balanced_boundaries(points, edges, count, limit, speed, mode, photo_allowance, budget, finish_action, locations):
    if count < 1 or count > max(1, len(points) - 1):
        return None
    n = len(points)
    prefix = [0.0]
    for distance in edges:
        prefix.append(prefix[-1] + distance)
    location_costs = []
    first_waypoint_recovery = {}
    for flight_index in range(count):
        location = _flight_location(locations, flight_index)
        outbound = [_distance(location["launch"], point) / speed
                    for point in points] if location.get("launch") else [0.0] * n
        recovery = ([_distance(point, location["home"]) / speed for point in points]
                    if finish_action == "return_to_home" and location.get("home") else [0.0] * n)
        location_costs.append((outbound, recovery))
    current = {0: (0.0, [])}
    for used in range(count):
        following = {}
        for start, (worst, boundaries) in current.items():
            remaining_groups = count - used - 1
            min_end = start if n == 1 else max(
                start + 1,
                n - 1 - remaining_groups * (limit - 1),
            )
            max_end = min(n - 1 - remaining_groups, start + limit - 1)
            for end in range(min_end, max_end + 1):
                seconds = (prefix[end] - prefix[start]) / speed
                if mode == "full_auto":
                    seconds += (end - start + 1) * photo_allowance + 3
                outbound, recovery = location_costs[used]
                seconds += outbound[start] + recovery[end]
                if finish_action == "return_to_first_waypoint":
                    pair = (start, end)
                    if pair not in first_waypoint_recovery:
                        first_waypoint_recovery[pair] = _distance(points[end], points[start]) / speed
                    seconds += first_waypoint_recovery[pair]
                if seconds > budget:
                    continue
                value = (max(worst, seconds), boundaries + [(start, end)])
                if end not in following or value[0] < following[end][0] - 1e-9:
                    following[end] = value
        current = following
        if not current:
            return None
    return current.get(n - 1, (None, None))[1]


def _segment_seconds(edges, start, end, speed, mode, photo_allowance):
    seconds = sum(edges[start:end]) / speed
    if mode == "full_auto":
        seconds += (end - start + 1) * photo_allowance + 3
    return seconds


def _flight_result(index, start, end, points, edges, speed, mode, interval, photo_allowance, budget, finish_action, location):
    route_distance = sum(edges[start:end])
    launch = location.get("launch") if location else None
    home = location.get("home") if location else None
    outbound = _distance(launch, points[start]) if launch else None
    recovery_required = finish_action in ("return_to_home", "return_to_first_waypoint")
    if finish_action == "return_to_first_waypoint":
        recovery = _distance(points[end], points[start])
    else:
        recovery = _distance(points[end], home) if recovery_required and home else (0.0 if not recovery_required else None)
    complete = outbound is not None and recovery is not None
    known_distance = route_distance + (outbound or 0) + (recovery or 0)
    capture_seconds = (end - start + 1) * photo_allowance if mode == "full_auto" else 0.0
    startup_seconds = 3.0 if mode == "full_auto" else 0.0
    known_seconds = known_distance / speed + capture_seconds + startup_seconds
    if mode == "full_auto":
        actions = []
        for waypoint_index in range(start, end + 1):
            if waypoint_index == start:
                actions.extend([
                    {"type": "rotate_camera", "waypoint_index": waypoint_index, "pitch_deg": -90},
                    {"type": "hover", "waypoint_index": waypoint_index, "duration_s": 3.0},
                ])
            actions.append({"type": "take_photo", "waypoint_index": waypoint_index})
        photo_count = end - start + 1
    else:
        actions = [{"type": "rotate_camera", "waypoint_index": start, "pitch_deg": -90}]
        photo_count = math.floor((route_distance / speed) / interval)
    return {
        "index": index,
        "route_start_waypoint_index": start,
        "route_end_waypoint_index": end,
        "waypoint_count": end - start + 1,
        "actions": actions,
        "route_distance_m": route_distance,
        "outbound_distance_m": outbound,
        "recovery_distance_m": recovery,
        "known_distance_m": known_distance,
        "total_distance_m": known_distance if complete else None,
        "capture_allowance_seconds": capture_seconds,
        "startup_wait_seconds": startup_seconds,
        "known_estimated_seconds": known_seconds,
        "total_estimated_seconds": known_seconds if complete else None,
        "usable_battery_seconds": budget,
        "estimates_complete": complete,
        "photo_count": photo_count,
        "photo_count_kind": "actions" if mode == "full_auto" else "estimate",
    }


def _locations(value, requested_flights):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PlanningError("invalid_locations", "locations must be an object.", "locations")
    _keys(value, {"shared", "per_flight"}, "locations")
    if "shared" in value and "per_flight" in value:
        raise PlanningError("invalid_locations", "Use shared or per-flight locations, not both.", "locations")
    if "shared" in value:
        return "shared", _location(value["shared"], "locations.shared")
    if "per_flight" in value:
        items = value["per_flight"]
        if not isinstance(items, list) or len(items) != requested_flights:
            raise PlanningError("per_flight_location_count_mismatch", "Per-flight locations must match requested_flights.", "locations.per_flight")
        return "per_flight", [_location(item, f"locations.per_flight.{index}") for index, item in enumerate(items)]
    raise PlanningError("invalid_locations", "locations must contain shared or per_flight.", "locations")


def _location(value, field):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PlanningError("invalid_location", "Flight location must be an object.", field)
    _keys(value, {"launch", "home"}, field)
    result = {}
    for key in ("launch", "home"):
        if value.get(key) is not None:
            result[key] = _position(value[key], f"{field}.{key}")
    return result


def _flight_location(locations, index):
    if locations is None:
        return {}
    kind, value = locations
    return value if kind == "shared" else (value[index] if index < len(value) else {})


def _known_location_seconds(points, start, end, speed, finish_action, location):
    seconds = 0.0
    if location and location.get("launch"):
        seconds += _distance(location["launch"], points[start]) / speed
    if finish_action == "return_to_home" and location and location.get("home"):
        seconds += _distance(points[end], location["home"]) / speed
    return seconds


def _distance(first, second):
    return route_distance_m([first, second])


def _position(value, field):
    if not isinstance(value, dict):
        raise PlanningError("invalid_position", "Position must be an object.", field)
    _keys(value, {"latitude_deg", "longitude_deg"}, field)
    latitude = _number(value.get("latitude_deg"), f"{field}.latitude_deg", minimum=-90, maximum=90)
    longitude = _number(value.get("longitude_deg"), f"{field}.longitude_deg", minimum=-180, maximum=180)
    return {"latitude_deg": latitude, "longitude_deg": longitude}


def _validate_area(value, field="survey_area"):
    if not isinstance(value, dict):
        raise PlanningError("invalid_geometry", "Survey area must be an object.", field)
    if "parts" in value:
        _keys(value, {"parts"}, field)
        parts = value["parts"]
        if not isinstance(parts, list) or not parts:
            raise PlanningError("invalid_geometry", "Survey area parts cannot be empty.", f"{field}.parts")
        for index, part in enumerate(parts):
            _validate_area(part, f"{field}.parts.{index}")
        return
    _keys(value, {"exterior", "holes"}, field)
    _validate_ring(value.get("exterior"), f"{field}.exterior")
    holes = value.get("holes", [])
    if not isinstance(holes, list):
        raise PlanningError("invalid_geometry", "Survey holes must be an array.", f"{field}.holes")
    for index, ring in enumerate(holes):
        _validate_ring(ring, f"{field}.holes.{index}")


def _validate_ring(value, field):
    if not isinstance(value, list) or len(value) < 3:
        raise PlanningError("invalid_geometry", "Polygon ring needs at least three positions.", field)
    for index, point in enumerate(value):
        _position(point, f"{field}.{index}")


def _number(value, field, minimum=None, maximum=None, exclusive=False, maximum_exclusive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlanningError("invalid_number", "Value must be a finite JSON number.", field)
    number = float(value)
    if minimum is not None and (number <= minimum if exclusive else number < minimum):
        raise PlanningError("number_out_of_range", "Value is below the allowed range.", field)
    if maximum is not None and (number >= maximum if maximum_exclusive else number > maximum):
        raise PlanningError("number_out_of_range", "Value is above the allowed range.", field)
    return number


def _optional_bool(value, field):
    if type(value) is not bool:
        raise PlanningError("invalid_boolean", "Value must be a boolean.", field)
    return value


def _keys(value, allowed, field):
    unknown = sorted(set(value) - allowed)
    if unknown:
        name = unknown[0]
        raise PlanningError("unknown_field", f"Unknown field: {name}.", f"{field}.{name}")


def _ensure_finite_json(value, field="result"):
    if isinstance(value, float) and not math.isfinite(value):
        raise PlanningError("non_finite_result", "Planning produced a non-finite number.", field)
    if isinstance(value, dict):
        for key, item in value.items():
            _ensure_finite_json(item, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _ensure_finite_json(item, f"{field}.{index}")


def _integer(value, field, minimum=None, maximum=None):
    number = _number(value, field, minimum=minimum, maximum=maximum)
    if not number.is_integer():
        raise PlanningError("invalid_integer", "Value must be an integer.", field)
    return int(number)


def _version(value, expected, field):
    try:
        actual = _integer(value, field, minimum=1)
    except PlanningError:
        raise PlanningError("unsupported_version", f"Supported {field} is {expected}.", field) from None
    if actual != expected:
        raise PlanningError("unsupported_version", f"Supported {field} is {expected}.", field)


def _issue(code, message):
    return {"code": code, "message": message}
