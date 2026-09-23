"""QGIS check for takeoff-zone computation through its public interface."""

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_split_and_shared_takeoff_policy():
    try:
        from FlyPath.takeoff_adapter import compute_zones
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc

    class MissingElevation(Exception):
        pass

    parts = [[(13.0, 52.0), (13.001, 52.0)],
             [(13.002, 52.0), (13.003, 52.0)]]
    sample = lambda _longitude, _latitude: 100.0
    separate = compute_zones(
        parts, sample, MissingElevation, tolerance_m=2, altitude_m=100,
        same_takeoff=False, radius_m=50, steps=4)
    shared = compute_zones(
        parts, sample, MissingElevation, tolerance_m=2, altitude_m=100,
        same_takeoff=True, radius_m=50, steps=4)

    assert len(separate['zones']) == 2
    assert len(shared['zones']) == 1
    assert all(zone['flat'] for zone in separate['zones'])
    assert separate['n_sampled'] > shared['n_sampled'] > 0
    assert separate['gsd_var'] == 2.0


if __name__ == '__main__':
    test_split_and_shared_takeoff_policy()
    print('Takeoff adapter check passed')
