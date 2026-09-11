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
        assert c.min_shoot_interval_s > 0, f'{d.name}: min_shoot_interval_s must be positive'


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


def test_unavailable_drone_hidden_from_list_but_still_registered():
    # The Matrice 4E is kept in the registry (writer/tests use it) but hidden
    # from the UI list until verified.
    assert 'DJI Matrice 4E' not in registry.names()
    assert registry.has('DJI Matrice 4E')
    assert registry.get('DJI Matrice 4E').available is False
    # Every name offered in the UI is an available drone.
    assert all(registry.get(n).available for n in registry.names())


def test_new_consumer_drones_use_the_shared_mini_enum():
    # Air 3 / Air 3S / Mavic 4 Pro ship selectable, on the assumption that DJI
    # Fly uses a shared consumer enum (68, the value verified on both the Mini 4
    # Pro and Mini 5 Pro). Field reports may prove a model needs its own enum; if
    # one is changed away from the shared value, update this test.
    shared = registry.get('DJI Mini 4 Pro').drone_enum
    assert registry.get('DJI Mini 5 Pro').drone_enum == shared     # the basis (68)
    for name in ('DJI Air 3', 'DJI Air 3S', 'DJI Mavic 4 Pro', 'DJI Mavic 3 Classic'):
        assert registry.has(name), f'{name} should be registered'
        d = registry.get(name)
        assert d.available is True, f'{name} should be selectable'
        assert name in registry.names()
        assert d.drone_enum == shared, (
            f'{name} ships with the shared consumer enum ({shared}); update this '
            f'test if a field report forces a model-specific value')


def test_every_consumer_drone_maps_to_a_website_drone_code():
    # The website sync (flypath_sync.py) pushes/pulls missions by the website's
    # own drone code, and reads it from here. A consumer drone without one could
    # not be sent at all; the enterprise Matrice 4E is not offered on the
    # website, so it has none on purpose.
    for d in registry.all_drones():
        if d.category == 'consumer':
            assert d.website_code, f'{d.name}: missing website_code'
            assert registry.name_for_website_code(d.website_code) == d.name
        else:
            assert d.website_code is None, f'{d.name}: unexpected website_code'


def test_website_codes_are_unique_and_match_the_live_site():
    codes = [d.website_code for d in registry.all_drones() if d.website_code]
    assert len(codes) == len(set(codes)), 'website codes must be unique'
    # The slugs seeded on flypath.io; a rename there needs a matching edit here.
    assert registry.get('DJI Mini 4 Pro').website_code == 'mini4pro'
    assert registry.get('DJI Mavic 3 Classic').website_code == 'mavic3classic'
    assert registry.name_for_website_code('not-a-real-drone') is None


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
