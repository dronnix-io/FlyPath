"""
Validation tests for the drone hardware registry (hardware/drones.json).

Pure Python, no QGIS needed. Run with pytest, or directly:
    python tests/test_hardware_registry.py
"""

import math
import os
import sys

# Make the plugin root importable so `hardware` resolves as a top-level package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hardware import registry  # noqa: E402
from hardware.models import Drone, Camera  # noqa: E402

VALID_CATEGORIES = ('consumer', 'enterprise')


def test_registry_loads_drones():
    assert registry.names(), 'registry has no drones'
    for name in registry.names():
        assert isinstance(registry.get(name), Drone)


def test_every_drone_is_complete_and_valid():
    for d in registry.all_drones():
        assert d.name and isinstance(d.name, str)
        assert d.category in VALID_CATEGORIES, f'{d.name}: bad category {d.category}'
        assert d.app, f'{d.name}: missing app'
        assert d.info, f'{d.name}: missing info'
        assert isinstance(d.drone_enum, int)
        assert isinstance(d.drone_sub_enum, int)
        assert d.min_speed_ms > 0, f'{d.name}: min_speed_ms must be positive'
        assert d.max_speed_ms >= d.min_speed_ms, f'{d.name}: max < min speed'
        assert d.battery_time_min > 0, f'{d.name}: battery_time_min must be positive'
        c = d.camera
        assert isinstance(c, Camera)
        for field, val in (
            ('sensor_width_mm', c.sensor_width_mm),
            ('sensor_height_mm', c.sensor_height_mm),
            ('focal_length_mm', c.focal_length_mm),
        ):
            assert val > 0, f'{d.name}: camera.{field} must be positive'
        assert c.image_width_px > 0 and c.image_height_px > 0


def test_names_are_unique():
    names = registry.names()
    assert len(names) == len(set(names))


def test_known_enum_values_are_stable():
    # Locks the DJI enum values so a bad edit to drones.json is caught.
    assert registry.get('DJI Mini 3 Pro').drone_enum == 97
    assert registry.get('DJI Mini 4 Pro').drone_enum == 68
    assert registry.get('DJI Mini 5 Pro').drone_enum == 68


def test_grid_specs_shape():
    d = registry.get('DJI Mini 4 Pro')
    specs = d.grid_specs()
    assert set(specs) == {'focal_length_mm', 'sensor_width_mm'}
    assert specs['focal_length_mm'] == d.camera.focal_length_mm
    assert specs['sensor_width_mm'] == d.camera.sensor_width_mm


def test_camera_math_matches_legacy_formulas():
    # Reproduces the pre-refactor GSD / footprint formulas exactly.
    c = registry.get('DJI Mini 4 Pro').camera
    alt = 100.0
    expected_gsd = (alt * c.sensor_width_mm * 100.0) / (c.focal_length_mm * c.image_width_px)
    assert math.isclose(c.gsd_cm_per_px(alt), expected_gsd, rel_tol=1e-9)
    assert math.isclose(c.footprint_across(alt), alt * c.sensor_width_mm / c.focal_length_mm)
    assert math.isclose(c.footprint_along(alt), alt * c.sensor_height_mm / c.focal_length_mm)


def test_speed_range():
    assert registry.get('DJI Mini 4 Pro').speed_range() == (1.0, 12.0)
    assert registry.get('DJI Mini 5 Pro').speed_range() == (1.0, 15.0)


def test_has_and_missing():
    assert registry.has('DJI Mini 4 Pro')
    assert not registry.has('Nonexistent Drone')


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f'PASS  {fn.__name__}')
        except AssertionError as exc:
            failed += 1
            print(f'FAIL  {fn.__name__}: {exc}')
    print(f'\n{len(fns) - failed}/{len(fns)} passed')
    sys.exit(1 if failed else 0)
