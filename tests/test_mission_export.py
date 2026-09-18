"""Mission export assembly checks."""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from FlyPath import mission_export  # noqa: E402
from FlyPath.hardware import registry  # noqa: E402


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
