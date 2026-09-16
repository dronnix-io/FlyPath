"""Assemble and write FlyPath missions without depending on the Qt dialog."""

import os
import shutil
import tempfile
from dataclasses import dataclass

from .wpml import MissionSpec, write_mission


@dataclass(frozen=True)
class ExportSettings:
    drone: object
    altitude_m: float
    speed_ms: float
    finish_action: str
    rc_lost_action: str
    gimbal_pitch: float
    polygon: list
    side_overlap: float
    front_overlap: float
    direction_deg: float
    margin_m: float
    capture_mode: str
    curved_path: bool
    launch_offset_m: float = 0.0


def write_kmz(filepath, settings, waypoints, mission_name, *,
              create_time_ms=None, heights=None, actions=None):
    """Write one mission, applying the launch datum to all written heights."""
    offset = settings.launch_offset_m
    spec = MissionSpec(
        waypoints=waypoints,
        altitude_m=settings.altitude_m - offset,
        speed_ms=settings.speed_ms,
        finish_action=settings.finish_action,
        rc_lost_action=settings.rc_lost_action,
        gimbal_pitch=settings.gimbal_pitch,
        mission_name=mission_name,
        create_time_ms=create_time_ms,
        polygon=settings.polygon,
        side_overlap=settings.side_overlap,
        front_overlap=settings.front_overlap,
        direction_deg=settings.direction_deg,
        margin_m=settings.margin_m,
        capture_mode=settings.capture_mode,
        heights=([height - offset for height in heights]
                 if heights is not None else None),
        curved_path=settings.curved_path,
        actions=actions,
    )
    write_mission(settings.drone, spec, filepath)


def write_local(filepath, mission_name, missions, settings, actions=None):
    """Write one KMZ per mission and return the concrete output targets."""
    count = len(missions)
    base, extension = os.path.splitext(filepath)
    extension = extension or '.kmz'
    targets = []
    for index, (waypoints, heights, _ground) in enumerate(missions, start=1):
        if count == 1:
            path, name = filepath, mission_name
        else:
            width = len(str(count))
            path = f'{base}_{str(index).zfill(width)}_of_{count}{extension}'
            name = f'{mission_name} {index} of {count}'
        flight_actions = actions[index - 1] if actions else None
        write_kmz(path, settings, waypoints, name, heights=heights,
                  actions=flight_actions)
        targets.append((path, waypoints, name, heights, flight_actions))
    return targets


def replace_folder(waypoint_path, uuid, settings, waypoints, mission_name, *,
                   create_time_ms=None, heights=None, actions=None):
    """Replace the selected mission in a filesystem-backed RC folder."""
    folder = os.path.join(waypoint_path, uuid)
    if not os.path.isdir(folder):
        return False, f'Mission folder not found:\n{folder}'
    try:
        write_kmz(os.path.join(folder, uuid + '.kmz'), settings, waypoints,
                  mission_name, create_time_ms=create_time_ms,
                  heights=heights, actions=actions)
    except Exception as exc:
        return False, str(exc)
    return True, uuid


def replace_controller(waypoint_path, uuid, settings, waypoints, mission_name,
                       *, create_time_ms=None, heights=None, actions=None,
                       mtp_copy=None):
    """Replace one RC slot, using a direct write or the supplied MTP copier."""
    if os.path.isdir(waypoint_path):
        return replace_folder(
            waypoint_path, uuid, settings, waypoints, mission_name,
            create_time_ms=create_time_ms, heights=heights, actions=actions)
    temp_dir = tempfile.mkdtemp(prefix='flypath_')
    try:
        kmz = os.path.join(temp_dir, uuid + '.kmz')
        try:
            write_kmz(kmz, settings, waypoints, mission_name,
                      create_time_ms=create_time_ms, heights=heights,
                      actions=actions)
        except Exception as exc:
            return False, f'Could not write KMZ: {exc}'
        return mtp_copy(waypoint_path, uuid, kmz, temp_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
