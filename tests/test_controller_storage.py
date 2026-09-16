"""Checks for controller mission discovery through its public interface."""

import os
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from FlyPath.controller_storage import list_missions_from_dir


def _mission(root, uuid, create_ms, coordinates):
    folder = os.path.join(root, uuid)
    os.makedirs(folder)
    template = '<wpml:createTime>%d</wpml:createTime>' % create_ms
    waylines = ''.join(
        '<Placemark><wpml:index>%d</wpml:index><coordinates>%s,%s</coordinates></Placemark>'
        % (index, longitude, latitude)
        for index, (longitude, latitude) in enumerate(coordinates))
    with zipfile.ZipFile(os.path.join(folder, uuid + '.kmz'), 'w') as archive:
        archive.writestr('wpmz/template.kml', template)
        archive.writestr('wpmz/waylines.wpml', waylines)


def test_lists_only_controller_tracked_missions_newest_first():
    older = '11111111-1111-1111-1111-111111111111'
    newer = '22222222-2222-2222-2222-222222222222'
    hidden = '33333333-3333-3333-3333-333333333333'
    with tempfile.TemporaryDirectory() as waypoint_dir:
        preview_dir = os.path.join(waypoint_dir, 'map_preview')
        os.makedirs(os.path.join(preview_dir, older))
        os.makedirs(os.path.join(preview_dir, newer))
        _mission(waypoint_dir, older, 1_700_000_000_000, [(10, 20)])
        _mission(waypoint_dir, newer, 1_800_000_000_000, [(30, 40), (31, 41)])
        _mission(waypoint_dir, hidden, 1_900_000_000_000, [(50, 60)])

        status, missions = list_missions_from_dir(waypoint_dir)

    assert status == 'ok'
    assert [mission['uuid'] for mission in missions] == [newer, older]
    assert missions[0]['n_wp'] == 2
    assert missions[0]['waypoints'] == [(30.0, 40.0), (31.0, 41.0)]


if __name__ == '__main__':
    test_lists_only_controller_tracked_missions_newest_first()
    print('Controller storage check passed')
