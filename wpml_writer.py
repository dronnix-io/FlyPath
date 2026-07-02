"""
wpml_writer.py
--------------
Writes a DJI-compatible WPML mission as a single .kmz file for 2D
orthomosaic mapping.

KMZ contents
------------
  <file>.kmz
  └── wpmz/
      ├── template.kml    — mission + wayline template config (no Placemarks)
      └── waylines.wpml   — mission config + all waypoint Placemarks

On the DJI RC each mission lives in waypoint/<uuid>/<uuid>.kmz; FlyPath
replaces that file with the .kmz written here.

Namespace        : http://www.uav.com/wpmz/1.0.2  (as used by DJI Fly on RC2)
Verified against : DJI Mini 4 Pro + DJI RC2 (native mission dump)
"""

import io
import time
import zipfile


# ── DJI drone enum values (verified from native RC2 mission files) ─────────
_DRONE_ENUM = {
    'DJI Mini 3 Pro': 97,   # community-verified
    'DJI Mini 4 Pro': 68,   # verified from native RC2 mission dump
    'DJI Mini 5 Pro': 68,   # community-verified: same enum as Mini 4 Pro, confirmed to fly on RC2
}

# ── Finish action mapping ──────────────────────────────────────────────────
_FINISH_ACTION = {
    'Return to Home':         'goHome',
    'Hover in place':         'hover',
    'Land at last waypoint':  'autoLand',
}

# ── RC lost action mapping ─────────────────────────────────────────────────
_RC_LOST_ACTION = {
    'Return to Home':   ('executeLostAction', 'goBack'),
    'Hover in place':   ('executeLostAction', 'hover'),
    'Land immediately': ('executeLostAction', 'landing'),
    'Continue mission': ('goContinue',        'goBack'),
}


# ── WPML namespace (native RC2 format) ────────────────────────────────────
_NS = 'http://www.uav.com/wpmz/1.0.2'


# ── Public API ─────────────────────────────────────────────────────────────

def write_kmz(filepath, waypoints, drone_name, altitude_m, speed_ms,
              finish_action_label, rc_lost_action_label,
              gimbal_pitch=-90, mission_name='FlyPath Mission',
              create_time_ms=None):
    """
    Write a single DJI-compatible KMZ file at filepath.

    FlyPath calls this both for local exports and when replacing a mission
    on the RC (it writes the KMZ, then copies it into the mission's UUID
    folder over USB).

    Parameters
    ----------
    filepath              : str   — destination .kmz path
    waypoints             : list of (lon, lat) float tuples in WGS84
    drone_name            : str   — key from DRONE_SPECS
    altitude_m            : float — AGL flight altitude in metres
    speed_ms              : float — waypoint flight speed in m/s
    finish_action_label   : str   — human-readable finish action label
    rc_lost_action_label  : str   — human-readable RC lost action label
    gimbal_pitch          : float — gimbal pitch angle in degrees (default -90)
    mission_name          : str   — embedded in mission metadata
    create_time_ms        : int   — preserve this createTime when replacing an
                                    existing mission, so its date keeps matching
                                    DJI Fly (None = use the current time)

    Raises
    ------
    ValueError  if waypoints is empty
    IOError     if the file cannot be written
    """
    if not waypoints:
        raise ValueError('No waypoints provided — define a survey area first.')

    drone_enum             = _DRONE_ENUM.get(drone_name, 68)
    finish_action          = _FINISH_ACTION.get(finish_action_label, 'goHome')
    height_mode            = 'relativeToStartPoint'
    exit_on_rc_lost, rc_lost_action = _RC_LOST_ACTION.get(
        rc_lost_action_label, ('executeLostAction', 'goBack')
    )
    ts_ms         = int(create_time_ms) if create_time_ms else int(time.time() * 1000)

    mission_config = _mission_config_xml(drone_enum, finish_action, speed_ms,
                                         exit_on_rc_lost, rc_lost_action)
    template_kml   = _build_template_kml(mission_config, ts_ms, mission_name,
                                         speed_ms, altitude_m, height_mode)
    waylines_wpml  = _build_waylines_wpml(
        waypoints, altitude_m, speed_ms, height_mode,
        gimbal_pitch, mission_config
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('wpmz/template.kml',  template_kml)
        zf.writestr('wpmz/waylines.wpml', waylines_wpml)

    with open(filepath, 'wb') as f:
        f.write(buf.getvalue())


# ── Shared mission config block ────────────────────────────────────────────

def _mission_config_xml(drone_enum, finish_action, speed_ms,
                        exit_on_rc_lost, rc_lost_action):
    transitional_speed = min(speed_ms, 5.0)
    return f'''    <wpml:missionConfig>
      <wpml:flyToWaylineMode>safely</wpml:flyToWaylineMode>
      <wpml:finishAction>{finish_action}</wpml:finishAction>
      <wpml:exitOnRCLost>{exit_on_rc_lost}</wpml:exitOnRCLost>
      <wpml:executeRCLostAction>{rc_lost_action}</wpml:executeRCLostAction>
      <wpml:globalTransitionalSpeed>{transitional_speed:.1f}</wpml:globalTransitionalSpeed>
      <wpml:droneInfo>
        <wpml:droneEnumValue>{drone_enum}</wpml:droneEnumValue>
        <wpml:droneSubEnumValue>0</wpml:droneSubEnumValue>
      </wpml:droneInfo>
    </wpml:missionConfig>'''


# ── XML builders ───────────────────────────────────────────────────────────

def _build_template_kml(mission_config, ts_ms, mission_name,
                        speed_ms, altitude_m, height_mode):
    """template.kml — mission config + wayline template Folder (required by DJI RC)."""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"
     xmlns:wpml="{_NS}">
  <Document>
    <wpml:author>{_esc(mission_name)}</wpml:author>
    <wpml:createTime>{ts_ms}</wpml:createTime>
    <wpml:updateTime>{ts_ms}</wpml:updateTime>
{mission_config}
    <Folder>
      <wpml:templateType>waypoint</wpml:templateType>
      <wpml:templateId>0</wpml:templateId>
      <wpml:waylineCoordinateSysParam>
        <wpml:coordinateMode>WGS84</wpml:coordinateMode>
        <wpml:heightMode>{height_mode}</wpml:heightMode>
        <wpml:positioningType>GPS</wpml:positioningType>
      </wpml:waylineCoordinateSysParam>
      <wpml:autoFlightSpeed>{speed_ms:.1f}</wpml:autoFlightSpeed>
      <wpml:globalHeight>{altitude_m:.1f}</wpml:globalHeight>
      <wpml:caliFlightEnable>0</wpml:caliFlightEnable>
      <wpml:gimbalPitchMode>usePointSetting</wpml:gimbalPitchMode>
    </Folder>
  </Document>
</kml>
'''


def _build_waylines_wpml(waypoints, altitude_m, speed_ms, height_mode,
                          gimbal_pitch, mission_config):
    """waylines.wpml — repeats missionConfig + full Placemark list."""
    placemark_blocks = []

    for idx, (lon, lat) in enumerate(waypoints):
        if idx == 0:
            action_groups = _gimbal_action_group(group_id=1, pitch_angle=gimbal_pitch)
        else:
            action_groups = ''
        placemark_blocks.append(
            _placemark(idx, lon, lat, altitude_m, speed_ms,
                       action_groups, gimbal_pitch)
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

def _placemark(idx, lon, lat, altitude_m, speed_ms, action_groups_xml,
               gimbal_pitch=-90):
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
          <wpml:waypointGimbalPitchAngle>{gimbal_pitch}</wpml:waypointGimbalPitchAngle>
          <wpml:waypointGimbalYawAngle>0</wpml:waypointGimbalYawAngle>
        </wpml:waypointGimbalHeadingParam>
      </Placemark>'''


def _gimbal_action_group(group_id, pitch_angle=-90):
    """Set gimbal pitch at waypoint 0."""
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
              <wpml:gimbalPitchRotateAngle>{pitch_angle}</wpml:gimbalPitchRotateAngle>
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



# ── Utilities ──────────────────────────────────────────────────────────────

def _esc(text):
    """Minimal XML text escaping."""
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))
