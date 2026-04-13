"""
wpml_writer.py
--------------
Generates a DJI WPML-compliant KMZ mission file for 2D orthomosaic mapping.

KMZ structure
-------------
  mission.kmz
  └── wpmz/
      ├── template.kml   — mission-level settings (drone model, speed, finish action)
      └── waylines.wpml  — turn-point waypoints with distance-based photo trigger

Photo triggering strategy
-------------------------
Waypoints are placed only at flight-line turn points (line endpoints).
The camera is fired automatically every `shot_spacing_m` metres along the
entire route via a single multipleDistance action group spanning all waypoints.
A gimbalRotate action at waypoint 0 sets the camera to nadir (-90°) before
the mission begins.

WPML namespace : http://www.dji.com/wpmz/1.0.6
Tested against : DJI Mini 3, Mini 3 Pro, Mini 4 Pro
"""

import io
import math
import time
import zipfile


# ── DJI drone enum values (from WPML spec / community verification) ────────
_DRONE_ENUM = {
    'DJI Mini 3':     97,
    'DJI Mini 3 Pro': 97,
    'DJI Mini 4 Pro': 144,
    'DJI Mini 5':     144,   # placeholder — update when officially documented
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


# ── Public API ─────────────────────────────────────────────────────────────

def write_kmz(filepath, waypoints, drone_name, altitude_m, speed_ms,
              finish_action_label, altitude_mode_label, shot_spacing_m,
              mission_name='FlyPath Mission'):
    """
    Write a DJI WPML KMZ file from the provided turn-point waypoints.

    Parameters
    ----------
    filepath            : str   — destination .kmz path (will be overwritten)
    waypoints           : list of (lon, lat) float tuples in WGS84 — turn points only
    drone_name          : str   — key from DRONE_SPECS, e.g. 'DJI Mini 4 Pro'
    altitude_m          : float — AGL flight altitude in metres
    speed_ms            : float — waypoint flight speed in m/s
    finish_action_label : str   — human-readable label from finishActionCombo
    altitude_mode_label : str   — human-readable label from altitudeModeCombo
    shot_spacing_m      : float — along-track distance between photos in metres
    mission_name        : str   — used in the author/title fields

    Raises
    ------
    ValueError  if waypoints is empty
    IOError     if the file cannot be written
    """
    if not waypoints:
        raise ValueError('No waypoints provided — define a survey area first.')

    drone_enum    = _DRONE_ENUM.get(drone_name, 144)
    finish_action = _FINISH_ACTION.get(finish_action_label, 'goHome')
    height_mode   = _HEIGHT_MODE.get(altitude_mode_label, 'relativeToStartPoint')
    ts_ms         = int(time.time() * 1000)

    total_dist_m = _path_length(waypoints)
    duration_s   = int(total_dist_m / speed_ms) if speed_ms > 0 else 0

    template_kml = _build_template_kml(
        drone_enum, finish_action, height_mode,
        speed_ms, total_dist_m, duration_s, ts_ms, mission_name
    )
    waylines_wpml = _build_waylines_wpml(
        waypoints, altitude_m, speed_ms, height_mode, shot_spacing_m
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('wpmz/template.kml',  template_kml)
        zf.writestr('wpmz/waylines.wpml', waylines_wpml)

    with open(filepath, 'wb') as f:
        f.write(buf.getvalue())


# ── XML builders ───────────────────────────────────────────────────────────

def _build_template_kml(drone_enum, finish_action, height_mode,
                         speed_ms, distance_m, duration_s, ts_ms, mission_name):
    transitional_speed = min(speed_ms, 5.0)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"
     xmlns:wpml="http://www.dji.com/wpmz/1.0.6">
  <Document>
    <wpml:author>{_esc(mission_name)}</wpml:author>
    <wpml:createTime>{ts_ms}</wpml:createTime>
    <wpml:updateTime>{ts_ms}</wpml:updateTime>
    <wpml:missionConfig>
      <wpml:flyToWaylineMode>safely</wpml:flyToWaylineMode>
      <wpml:finishAction>{finish_action}</wpml:finishAction>
      <wpml:exitOnRCLost>goContinue</wpml:exitOnRCLost>
      <wpml:executeRCLostAction>hover</wpml:executeRCLostAction>
      <wpml:globalTransitionalSpeed>{transitional_speed:.1f}</wpml:globalTransitionalSpeed>
      <wpml:droneInfo>
        <wpml:droneEnumValue>{drone_enum}</wpml:droneEnumValue>
        <wpml:droneSubEnumValue>0</wpml:droneSubEnumValue>
      </wpml:droneInfo>
    </wpml:missionConfig>
    <Folder>
      <wpml:templateId>0</wpml:templateId>
      <wpml:executeHeightMode>{height_mode}</wpml:executeHeightMode>
      <wpml:waylineId>0</wpml:waylineId>
      <wpml:distance>{distance_m:.1f}</wpml:distance>
      <wpml:duration>{duration_s}</wpml:duration>
      <wpml:autoFlightSpeed>{speed_ms:.1f}</wpml:autoFlightSpeed>
    </Folder>
  </Document>
</kml>
'''


def _build_waylines_wpml(waypoints, altitude_m, speed_ms, height_mode, shot_spacing_m):
    last_idx = len(waypoints) - 1
    placemark_blocks = []

    for idx, (lon, lat) in enumerate(waypoints):
        if idx == 0:
            # Waypoint 0: gimbal-rotate action + distance-based photo trigger
            action_groups = (
                _gimbal_action_group(group_id=0) +
                _distance_photo_group(group_id=1,
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
     xmlns:wpml="http://www.dji.com/wpmz/1.0.6">
  <Document>
    <Folder>
      <wpml:templateId>0</wpml:templateId>
      <wpml:executeHeightMode>{height_mode}</wpml:executeHeightMode>
      <wpml:waylineId>0</wpml:waylineId>
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
          <coordinates>{lon:.8f},{lat:.8f},0</coordinates>
        </Point>
        <wpml:index>{idx}</wpml:index>
        <wpml:executeHeight>{altitude_m:.1f}</wpml:executeHeight>
        <wpml:waypointSpeed>{speed_ms:.1f}</wpml:waypointSpeed>
        <wpml:waypointHeadingParam>
          <wpml:waypointHeadingMode>followWayline</wpml:waypointHeadingMode>
        </wpml:waypointHeadingParam>
        <wpml:waypointTurnParam>
          <wpml:waypointTurnMode>toPointAndStopWithContinuityCurvature</wpml:waypointTurnMode>
          <wpml:waypointTurnDampingDist>0</wpml:waypointTurnDampingDist>
        </wpml:waypointTurnParam>
{action_groups_xml}      </Placemark>'''


def _gimbal_action_group(group_id):
    """Action group: set gimbal to nadir at waypoint 0 (reachPoint trigger)."""
    return f'''        <wpml:actionGroup>
          <wpml:actionGroupId>{group_id}</wpml:actionGroupId>
          <wpml:actionGroupStartIndex>0</wpml:actionGroupStartIndex>
          <wpml:actionGroupEndIndex>0</wpml:actionGroupEndIndex>
          <wpml:actionGroupMode>sequence</wpml:actionGroupMode>
          <wpml:actionTrigger>
            <wpml:actionTriggerType>reachPoint</wpml:actionTriggerType>
          </wpml:actionTrigger>
          <wpml:action>
            <wpml:actionId>0</wpml:actionId>
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
    """Action group: fire camera every spacing_m metres along the entire route."""
    return f'''        <wpml:actionGroup>
          <wpml:actionGroupId>{group_id}</wpml:actionGroupId>
          <wpml:actionGroupStartIndex>{start_idx}</wpml:actionGroupStartIndex>
          <wpml:actionGroupEndIndex>{end_idx}</wpml:actionGroupEndIndex>
          <wpml:actionGroupMode>sequence</wpml:actionGroupMode>
          <wpml:actionTrigger>
            <wpml:actionTriggerType>multipleDistance</wpml:actionTriggerType>
            <wpml:actionTriggerParam>{spacing_m:.2f}</wpml:actionTriggerParam>
          </wpml:actionTrigger>
          <wpml:action>
            <wpml:actionId>0</wpml:actionId>
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
