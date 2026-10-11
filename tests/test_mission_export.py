"""Mission export assembly checks."""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from FlyPath import mission_export  # noqa: E402
from FlyPath.hardware import registry  # noqa: E402


def test_orbit_headings_travel_with_the_export_settings():
    captured = []
    original = mission_export.write_mission
    mission_export.write_mission = (
        lambda drone, spec, path: captured.append(spec))
    try:
        settings = mission_export.ExportSettings(
            drone=registry.get('DJI Mini 4 Pro'), altitude_m=25, speed_ms=3,
            finish_action='Return to Home', rc_lost_action='Return to Home',
            gimbal_pitch=-35, polygon=None, side_overlap=.9, front_overlap=.7,
            direction_deg=0, margin_m=0, capture_mode='full', curved_path=True,
            headings=(180.0, -90.0))
        mission_export.write_kmz('orbit.kmz', settings, [(0, 0), (1, 1)], 'Orbit')
    finally:
        mission_export.write_mission = original
    assert captured[0].headings == [180.0, -90.0]
    assert captured[0].gimbal_pitch == -35
    assert captured[0].heights is None and captured[0].gimbal_pitches is None


def test_multi_ring_orbit_writes_each_ring_height_and_tilt():
    from FlyPath import planning_adapter
    import zipfile
    rings = [(30, 25, -20), (20, 45, -60)]
    planned = planning_adapter.build_orbit_request(
        centre=(-114.0, 51.0), drone_profile_id='mini4pro', rings=rings,
        clockwise=True, speed_m_s=3, capture_mode='full', side_overlap_ratio=.9,
        finish_action='Return to Home', max_waypoints_per_flight=200)
    result = planning_adapter.plan(planned)
    flight = planning_adapter.consume_result(planned, result)[0]
    settings = mission_export.ExportSettings(
        drone=registry.get('DJI Mini 4 Pro'), altitude_m=25, speed_ms=3,
        finish_action='Return to Home', rc_lost_action='Return to Home',
        gimbal_pitch=-20, polygon=None, side_overlap=.9, front_overlap=.7,
        direction_deg=0, margin_m=0, capture_mode='full', curved_path=True,
        headings=tuple(flight['headings']), heights=tuple(flight['heights']),
        gimbal_pitches=tuple(flight['gimbal_pitches']))
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / 'rings.kmz')
        mission_export.write_kmz(path, settings, flight['waypoints'], 'Rings',
                                 actions=flight['actions'])
        with zipfile.ZipFile(path) as archive:
            wpml = archive.read('wpmz/waylines.wpml').decode('utf-8')
    rows = result['route']['waypoints']
    first = [row['ring_index'] for row in rows].count(0)
    heights = [float(text.split('<')[0])
               for text in wpml.split('<wpml:executeHeight>')[1:]]
    assert heights == [25.0] * first + [45.0] * (len(rows) - first)
    assert wpml.count('<wpml:waypointGimbalPitchAngle>-20<') == first
    assert wpml.count('<wpml:waypointGimbalPitchAngle>-60<') == len(rows) - first
    # Each ring turns the camera to its own tilt where it starts.
    assert '<wpml:gimbalPitchRotateAngle>-20.0</wpml:gimbalPitchRotateAngle>' in wpml
    assert '<wpml:gimbalPitchRotateAngle>-60.0</wpml:gimbalPitchRotateAngle>' in wpml


def test_split_flights_can_carry_their_own_settings():
    import dataclasses
    captured = []
    original = mission_export.write_mission
    mission_export.write_mission = (
        lambda drone, spec, path: captured.append(spec))
    settings = mission_export.ExportSettings(
        drone=registry.get('DJI Mini 4 Pro'), altitude_m=25, speed_ms=3,
        finish_action='Return to Home', rc_lost_action='Return to Home',
        gimbal_pitch=-20, polygon=None, side_overlap=.9, front_overlap=.7,
        direction_deg=0, margin_m=0, capture_mode='full', curved_path=True)
    per_flight = [dataclasses.replace(settings, headings=(10.0, 20.0), heights=(25, 25),
                                      gimbal_pitches=(-20, -20)),
                  dataclasses.replace(settings, altitude_m=40, headings=(30.0,), heights=(40,),
                                      gimbal_pitches=(-60,))]
    try:
        with tempfile.TemporaryDirectory() as folder:
            mission_export.write_local(
                str(Path(folder) / 'orbit.kmz'), 'Orbit',
                [([(0, 0), (1, 1)], None, None), ([(2, 2)], None, None)], settings,
                flight_settings=per_flight)
    finally:
        mission_export.write_mission = original
    assert [spec.headings for spec in captured] == [[10.0, 20.0], [30.0]]
    assert [spec.heights for spec in captured] == [[25, 25], [40]]
    assert [spec.gimbal_pitches for spec in captured] == [[-20, -20], [-60]]
    assert captured[1].altitude_m == 40


def test_split_export_names_and_applies_launch_offset():
    captured = []
    original = mission_export.write_mission
    mission_export.write_mission = (
        lambda drone, spec, path: captured.append((drone, spec, path)))
    settings = mission_export.ExportSettings(
        drone=registry.get('DJI Mini 3 Pro'), altitude_m=80, speed_ms=7,
        finish_action='goHome', rc_lost_action='goContinue', gimbal_pitch=-90,
        polygon=[], side_overlap=.7, front_overlap=.8, direction_deg=12,
        margin_m=5, capture_mode='full', curved_path=True,
        launch_offset_m=10,
    )
    missions = [
        ([(1, 2), (3, 4)], [80, 82], None),
        ([(5, 6)], [85], None),
    ]
    try:
        with tempfile.TemporaryDirectory() as folder:
            targets = mission_export.write_local(
                str(Path(folder) / 'survey.kmz'), 'Survey', missions, settings,
                actions=[['first'], ['second']])
    finally:
        mission_export.write_mission = original

    assert [Path(item[0]).name for item in targets] == [
        'survey_1_of_2.kmz', 'survey_2_of_2.kmz']
    assert [item[1].mission_name for item in captured] == [
        'Survey 1 of 2', 'Survey 2 of 2']
    assert captured[0][1].altitude_m == 70
    assert captured[0][1].heights == [70, 72]
    assert captured[1][1].actions == ['second']


if __name__ == '__main__':
    test_split_export_names_and_applies_launch_offset()
    print('mission export tests passed')
