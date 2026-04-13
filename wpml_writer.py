"""
wpml_writer.py
--------------
Generates a DJI-compatible waypoint mission folder for 2D orthomosaic mapping.

Output structure (matches native DJI Fly RC2 format exactly)
------------------------------------------------------------
  <output_dir>/
  └── <uuid>/
      ├── <uuid>.kmz          — zipped WPML mission
      │   └── wpmz/
      │       ├── template.kml    — mission config only (no Placemarks)
      │       └── waylines.wpml   — mission config + all waypoints
      └── image/
          └── ShotSnap.json       — empty index required by DJI Fly

Namespace  : http://www.uav.com/wpmz/1.0.2  (as used by DJI Fly on RC2)
Verified against : DJI Mini 4 Pro + DJI RC2 (native mission dump)
"""

import io
import math
import time
import zipfile


# ── DJI drone enum values (verified from native RC2 mission files) ─────────
_DRONE_ENUM = {
    'DJI Mini 3':     97,   # community-verified
    'DJI Mini 3 Pro': 97,   # community-verified
    'DJI Mini 4 Pro': 68,   # verified from native RC2 mission dump
}

# ── Finish action mapping ──────────────────────────────────────────────────
_FINISH_ACTION = {
    'Return to Home':         'goHome',
    'Hover in place':         'hover',
    'Land at last waypoint':  'autoLand',
}

# ── Altitude mode mapping ──────────────────────────────────────────────────
_HEIGHT_MODE = {
    'AGL  (Relative to takeoff)': 'relativeToStartPoint',
    'MSL  (Absolute)':            'WGS84',
}

# ── WPML namespace (native RC2 format) ────────────────────────────────────
_NS = 'http://www.uav.com/wpmz/1.0.2'


# ── Public API ─────────────────────────────────────────────────────────────

def write_kmz(filepath, waypoints, drone_name, altitude_m, speed_ms,
              finish_action_label, altitude_mode_label, shot_spacing_m,
              mission_name='FlyPath Mission'):
    """
    Write a single DJI-compatible KMZ file.

    To use on the RC2:
      1. Create a dummy waypoint mission on the RC in DJI Fly and note its UUID.
      2. Export from FlyPath — save the .kmz anywhere on your PC.
      3. Rename the exported file to <UUID>.kmz (matching the RC mission UUID).
      4. Copy it into the RC's waypoint/<UUID>/ folder, replacing the original.

    Parameters
    ----------
    filepath            : str   — destination .kmz path
    waypoints           : list of (lon, lat) float tuples in WGS84
    drone_name          : str   — key from DRONE_SPECS
    altitude_m          : float — AGL flight altitude in metres
    speed_ms            : float — waypoint flight speed in m/s
    finish_action_label : str   — human-readable finish action label
    altitude_mode_label : str   — human-readable altitude mode label
    shot_spacing_m      : float — along-track distance between photos in metres
    mission_name        : str   — embedded in mission metadata

    Raises
    ------
    ValueError  if waypoints is empty
    IOError     if the file cannot be written
    """
    if not waypoints:
        raise ValueError('No waypoints provided — define a survey area first.')

    drone_enum    = _DRONE_ENUM.get(drone_name, 68)
    finish_action = _FINISH_ACTION.get(finish_action_label, 'goHome')
    height_mode   = _HEIGHT_MODE.get(altitude_mode_label, 'relativeToStartPoint')
    ts_ms         = int(time.time() * 1000)

    mission_config = _mission_config_xml(drone_enum, finish_action, speed_ms)
    template_kml   = _build_template_kml(mission_config, ts_ms, mission_name)
    waylines_wpml  = _build_waylines_wpml(
        waypoints, altitude_m, speed_ms, height_mode,
        shot_spacing_m, mission_config
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('wpmz/template.kml',  template_kml)
        zf.writestr('wpmz/waylines.wpml', waylines_wpml)

    with open(filepath, 'wb') as f:
        f.write(buf.getvalue())


# ── Shared mission config block ────────────────────────────────────────────

def _mission_config_xml(drone_enum, finish_action, speed_ms):
    transitional_speed = min(speed_ms, 5.0)
    return f'''    <wpml:missionConfig>
      <wpml:flyToWaylineMode>safely</wpml:flyToWaylineMode>
      <wpml:finishAction>{finish_action}</wpml:finishAction>
      <wpml:exitOnRCLost>executeLostAction</wpml:exitOnRCLost>
      <wpml:executeRCLostAction>goBack</wpml:executeRCLostAction>
      <wpml:globalTransitionalSpeed>{transitional_speed:.1f}</wpml:globalTransitionalSpeed>
      <wpml:droneInfo>
        <wpml:droneEnumValue>{drone_enum}</wpml:droneEnumValue>
        <wpml:droneSubEnumValue>0</wpml:droneSubEnumValue>
      </wpml:droneInfo>
    </wpml:missionConfig>'''


# ── XML builders ───────────────────────────────────────────────────────────

def _build_template_kml(mission_config, ts_ms, mission_name):
    """template.kml — mission config only, no Placemarks (matches native format)."""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"
     xmlns:wpml="{_NS}">
  <Document>
    <wpml:author>{_esc(mission_name)}</wpml:author>
    <wpml:createTime>{ts_ms}</wpml:createTime>
    <wpml:updateTime>{ts_ms}</wpml:updateTime>
{mission_config}
  </Document>
</kml>
'''


def _build_waylines_wpml(waypoints, altitude_m, speed_ms, height_mode,
                          shot_spacing_m, mission_config):
    """waylines.wpml — repeats missionConfig + full Placemark list."""
    last_idx = len(waypoints) - 1
    placemark_blocks = []

    for idx, (lon, lat) in enumerate(waypoints):
        if idx == 0:
            action_groups = (
                _gimbal_action_group(group_id=1) +
                _distance_photo_group(group_id=2,
                                      start_idx=0,
                                      end_idx=last_idx,
                                      spacing_m=shot_spacing_m)
            )
        else:
            action_groups = ''
        placemark_blocks.append(
            _placemark(idx, lon, lat, altitude_m, speed_ms, action_groups)
        )

    placemarks = '\n'.join(placemark_blocks)

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"
     xmlns:wpml="{_NS}">
  <Document>
{mission_config}
    <Folder>
      <wpml:templateId>0</wpml:templateId>
      <wpml:executeHeightMode>{height_mode}</wpml:executeHeightMode>
      <wpml:waylineId>0</wpml:waylineId>
      <wpml:distance>0</wpml:distance>
      <wpml:duration>0</wpml:duration>
      <wpml:autoFlightSpeed>{speed_ms:.1f}</wpml:autoFlightSpeed>
{placemarks}
    </Folder>
  </Document>
</kml>
'''


# ── Element helpers ────────────────────────────────────────────────────────

def _placemark(idx, lon, lat, altitude_m, speed_ms, action_groups_xml):
    return f'''      <Placemark>
        <Point>
          <coordinates>
            {lon:.8f},{lat:.8f}
          </coordinates>
        </Point>
        <wpml:index>{idx}</wpml:index>
        <wpml:executeHeight>{altitude_m:.1f}</wpml:executeHeight>
        <wpml:waypointSpeed>{speed_ms:.1f}</wpml:waypointSpeed>
        <wpml:waypointHeadingParam>
          <wpml:waypointHeadingMode>followWayline</wpml:waypointHeadingMode>
          <wpml:waypointHeadingAngle>0</wpml:waypointHeadingAngle>
          <wpml:waypointPoiPoint>0.000000,0.000000,0.000000</wpml:waypointPoiPoint>
          <wpml:waypointHeadingAngleEnable>0</wpml:waypointHeadingAngleEnable>
          <wpml:waypointHeadingPathMode>followBadArc</wpml:waypointHeadingPathMode>
          <wpml:waypointHeadingPoiIndex>0</wpml:waypointHeadingPoiIndex>
        </wpml:waypointHeadingParam>
        <wpml:waypointTurnParam>
          <wpml:waypointTurnMode>toPointAndStopWithContinuityCurvature</wpml:waypointTurnMode>
          <wpml:waypointTurnDampingDist>0</wpml:waypointTurnDampingDist>
        </wpml:waypointTurnParam>
        <wpml:useStraightLine>0</wpml:useStraightLine>
{action_groups_xml}        <wpml:waypointGimbalHeadingParam>
          <wpml:waypointGimbalPitchAngle>-90</wpml:waypointGimbalPitchAngle>
          <wpml:waypointGimbalYawAngle>0</wpml:waypointGimbalYawAngle>
        </wpml:waypointGimbalHeadingParam>
      </Placemark>'''


def _gimbal_action_group(group_id):
    """Set gimbal to nadir (-90°) at waypoint 0."""
    return f'''        <wpml:actionGroup>
          <wpml:actionGroupId>{group_id}</wpml:actionGroupId>
          <wpml:actionGroupStartIndex>0</wpml:actionGroupStartIndex>
          <wpml:actionGroupEndIndex>0</wpml:actionGroupEndIndex>
          <wpml:actionGroupMode>parallel</wpml:actionGroupMode>
          <wpml:actionTrigger>
            <wpml:actionTriggerType>reachPoint</wpml:actionTriggerType>
          </wpml:actionTrigger>
          <wpml:action>
            <wpml:actionId>{group_id}</wpml:actionId>
            <wpml:actionActuatorFunc>gimbalRotate</wpml:actionActuatorFunc>
            <wpml:actionActuatorFuncParam>
              <wpml:gimbalHeadingYawBase>aircraft</wpml:gimbalHeadingYawBase>
              <wpml:gimbalRotateMode>absoluteAngle</wpml:gimbalRotateMode>
              <wpml:gimbalPitchRotateEnable>1</wpml:gimbalPitchRotateEnable>
              <wpml:gimbalPitchRotateAngle>-90</wpml:gimbalPitchRotateAngle>
              <wpml:gimbalRollRotateEnable>0</wpml:gimbalRollRotateEnable>
              <wpml:gimbalRollRotateAngle>0</wpml:gimbalRollRotateAngle>
              <wpml:gimbalYawRotateEnable>0</wpml:gimbalYawRotateEnable>
              <wpml:gimbalYawRotateAngle>0</wpml:gimbalYawRotateAngle>
              <wpml:gimbalRotateTimeEnable>0</wpml:gimbalRotateTimeEnable>
              <wpml:gimbalRotateTime>0</wpml:gimbalRotateTime>
              <wpml:payloadPositionIndex>0</wpml:payloadPositionIndex>
            </wpml:actionActuatorFuncParam>
          </wpml:action>
        </wpml:actionGroup>
'''


def _distance_photo_group(group_id, start_idx, end_idx, spacing_m):
    """Fire camera every spacing_m metres along the entire route."""
    return f'''        <wpml:actionGroup>
          <wpml:actionGroupId>{group_id}</wpml:actionGroupId>
          <wpml:actionGroupStartIndex>{start_idx}</wpml:actionGroupStartIndex>
          <wpml:actionGroupEndIndex>{end_idx}</wpml:actionGroupEndIndex>
          <wpml:actionGroupMode>parallel</wpml:actionGroupMode>
          <wpml:actionTrigger>
            <wpml:actionTriggerType>multipleDistance</wpml:actionTriggerType>
            <wpml:actionTriggerParam>{spacing_m:.2f}</wpml:actionTriggerParam>
          </wpml:actionTrigger>
          <wpml:action>
            <wpml:actionId>{group_id}</wpml:actionId>
            <wpml:actionActuatorFunc>takePhoto</wpml:actionActuatorFunc>
            <wpml:actionActuatorFuncParam>
              <wpml:fileSuffix>flypath</wpml:fileSuffix>
              <wpml:payloadPositionIndex>0</wpml:payloadPositionIndex>
            </wpml:actionActuatorFuncParam>
          </wpml:action>
        </wpml:actionGroup>
'''


# ── Utilities ──────────────────────────────────────────────────────────────

def _path_length(waypoints):
    """Total great-circle path length in metres."""
    if len(waypoints) < 2:
        return 0.0
    total = 0.0
    R = 6_371_000.0
    for (lon1, lat1), (lon2, lat2) in zip(waypoints, waypoints[1:]):
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon / 2) ** 2)
        total += R * 2 * math.asin(math.sqrt(a))
    return total


def _esc(text):
    """Minimal XML text escaping."""
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))
