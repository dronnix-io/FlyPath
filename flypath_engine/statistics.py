"""Mission statistics shared by FlyPath clients."""

import math


def mission_statistics(*, route_distance_m, speed_m_s, capture_mode,
                       photo_interval_s, battery_minutes, waypoint_count=0,
                       stop_seconds=2.5):
    """Return route-only time, capture, and battery estimates."""
    distance = float(route_distance_m)
    speed = float(speed_m_s)
    interval = float(photo_interval_s)
    battery_seconds = float(battery_minutes) * 60
    if distance < 0 or speed <= 0 or interval <= 0 or battery_seconds <= 0:
        raise ValueError("Distance cannot be negative; speed, interval, and battery time must be positive.")
    if capture_mode not in ("semi", "full"):
        raise ValueError("Capture mode must be 'semi' or 'full'.")

    photos = int(waypoint_count) if capture_mode == "full" else int(distance / (speed * interval))
    seconds = distance / speed
    if capture_mode == "full":
        seconds += photos * max(float(stop_seconds), interval)
    return {
        "distance_m": distance,
        "flight_seconds": seconds,
        "photo_count": photos,
        "battery_count": math.ceil(seconds / battery_seconds) if seconds else 0,
    }
