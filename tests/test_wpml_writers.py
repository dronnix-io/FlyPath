"""
Tests for the WPML writer package (wpml/).

Pure Python, no QGIS. Run with pytest, or directly:
    python tests/test_wpml_writers.py
"""

import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hardware import registry              # noqa: E402
from hardware.models import Camera, Drone  # noqa: E402
from wpml import MissionSpec, write_mission  # noqa: E402
from wpml import factory                   # noqa: E402

WPS = [(-114.0, 51.0), (-114.0, 51.001), (-113.999, 51.001), (-113.999, 51.0)]


def _spec(**kw):
    base = dict(waypoints=WPS, altitude_m=100.0, speed_ms=5.0,
                finish_action='Return to Home', rc_lost_action='Return to Home',
                gimbal_pitch=-90, mission_name='Test', create_time_ms=1700000000000)
    base.update(kw)
    return MissionSpec(**base)


def _write(drone, spec):
    path = os.path.join(tempfile.mkdtemp(), 'm.kmz')
    write_mission(drone, spec, path)
    return path


def test_consumer_kmz_structure_and_enum():
    drone = registry.get('DJI Mini 4 Pro')
    path = _write(drone, _spec())
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        assert names == {'wpmz/template.kml', 'wpmz/waylines.wpml'}
        tpl = z.read('wpmz/template.kml').decode('utf-8')
    assert 'http://www.uav.com/wpmz/1.0.2' in tpl
    assert f'<wpml:droneEnumValue>{drone.drone_enum}</wpml:droneEnumValue>' in tpl
    assert '<wpml:templateType>waypoint</wpml:templateType>' in tpl


def test_enum_follows_the_drone():
    for name in ('DJI Mini 3 Pro', 'DJI Mini 4 Pro', 'DJI Mini 5 Pro'):
        drone = registry.get(name)
        path = _write(drone, _spec())
        with zipfile.ZipFile(path) as z:
            tpl = z.read('wpmz/template.kml').decode('utf-8')
        assert f'<wpml:droneEnumValue>{drone.drone_enum}</wpml:droneEnumValue>' in tpl


def test_empty_waypoints_raises():
    drone = registry.get('DJI Mini 4 Pro')
    try:
        _write(drone, _spec(waypoints=[]))
        assert False, 'expected ValueError for empty waypoints'
    except ValueError:
        pass


def test_factory_rejects_unknown_category():
    fake = Drone(name='X', category='spaceship', app='?',
                 drone_enum=0, drone_sub_enum=0, max_speed_ms=5, battery_time_min=10,
                 camera=Camera(1, 1, 1, 100, 100), info='x')
    try:
        write_mission(fake, _spec(), os.path.join(tempfile.mkdtemp(), 'm.kmz'))
        assert False, 'expected ValueError for unknown category'
    except ValueError:
        pass


def test_consumer_is_registered():
    assert 'consumer' in factory._WRITERS


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
