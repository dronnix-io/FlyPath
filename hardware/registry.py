"""
hardware/registry.py
--------------------
Single source of truth for the drones FlyPath supports.

Loads hardware/drones.json once at import, validates every entry, and exposes
lookups. Both the UI (flypath_dialog.py) and the WPML writer read drone data
from here, so a drone is defined in exactly one place.

Bad or incomplete data raises at load time (surfaced by tests/CI), never
silently at the user's runtime.
"""

import json
import os

from .models import Camera, Drone

_DATA_FILE = os.path.join(os.path.dirname(__file__), 'drones.json')

_VALID_CATEGORIES = ('consumer', 'enterprise')
_REQUIRED_AIRCRAFT = ('drone_enum', 'drone_sub_enum', 'max_speed_ms',
                      'battery_time_min')
_REQUIRED_CAMERA = ('sensor_width_mm', 'sensor_height_mm', 'focal_length_mm',
                    'image_width_px', 'image_height_px')


def _require(name, section, data, keys):
    missing = [k for k in keys if k not in data]
    if missing:
        raise ValueError(
            f'Drone "{name}": missing {section} field(s): {", ".join(missing)}')


def _build(name, entry):
    """Validate a raw JSON entry and build a Drone, or raise ValueError."""
    category = entry.get('category')
    if category not in _VALID_CATEGORIES:
        raise ValueError(
            f'Drone "{name}": category must be one of {_VALID_CATEGORIES}, '
            f'got {category!r}')
    for key in ('app', 'info'):
        if not entry.get(key):
            raise ValueError(f'Drone "{name}": missing field "{key}"')

    aircraft = entry.get('aircraft', {})
    cam = entry.get('camera', {})
    _require(name, 'aircraft', aircraft, _REQUIRED_AIRCRAFT)
    _require(name, 'camera', cam, _REQUIRED_CAMERA)

    min_speed = float(aircraft.get('min_speed_ms', 1.0))
    max_speed = float(aircraft['max_speed_ms'])
    if min_speed <= 0 or max_speed < min_speed:
        raise ValueError(
            f'Drone "{name}": need 0 < min_speed_ms <= max_speed_ms '
            f'(got min={min_speed}, max={max_speed})')

    camera = Camera(
        sensor_width_mm=float(cam['sensor_width_mm']),
        sensor_height_mm=float(cam['sensor_height_mm']),
        focal_length_mm=float(cam['focal_length_mm']),
        image_width_px=int(cam['image_width_px']),
        image_height_px=int(cam['image_height_px']),
        min_shoot_interval_s=float(cam.get('min_shoot_interval_s', 2.0)),
        payload_enum=int(cam.get('payload_enum', 0)),
        payload_sub_enum=int(cam.get('payload_sub_enum', 0)),
        payload_position_index=int(cam.get('payload_position_index', 0)),
    )
    return Drone(
        name=name,
        category=category,
        app=entry['app'],
        drone_enum=int(aircraft['drone_enum']),
        drone_sub_enum=int(aircraft['drone_sub_enum']),
        min_speed_ms=min_speed,
        max_speed_ms=max_speed,
        battery_time_min=int(aircraft['battery_time_min']),
        camera=camera,
        info=entry['info'],
        verified=entry.get('verified', ''),
        available=bool(entry.get('available', True)),
        signal_range_km=(float(aircraft['signal_range_km'])
                         if 'signal_range_km' in aircraft else None),
    )


def _load(path=_DATA_FILE):
    with open(path, encoding='utf-8') as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f'{path}: expected a non-empty object of drones.')
    return {name: _build(name, entry) for name, entry in raw.items()}


# Loaded once at import. JSON preserves insertion order, so names() keeps the
# drop-down order defined in drones.json.
_DRONES = _load()


def names():
    """Names of drones offered in the UI, in display order.

    Excludes drones flagged available=false (kept in the registry but not shown
    to users yet). Use all_drones()/get() to reach every registered drone."""
    return [name for name, d in _DRONES.items() if d.available]


def has(name):
    """True if a drone by this name is registered (available or not)."""
    return name in _DRONES


def get(name):
    """Return the Drone for this name; raises KeyError if unknown."""
    return _DRONES[name]


def all_drones():
    """All Drone objects."""
    return list(_DRONES.values())
