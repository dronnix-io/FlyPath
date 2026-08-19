"""
wpml/consumer.py
----------------
Mission writer for DJI consumer drones (DJI Fly on RC2).

Native WPML waypoint format, namespace http://www.uav.com/wpmz/1.0.2, verified
against a DJI Mini 4 Pro + DJI RC2 native mission dump. Emits wpmz/template.kml
+ wpmz/waylines.wpml packaged into a .kmz.
"""

import time

from .base import esc, package_kmz

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


def write(drone, spec, filepath):
    """Write a consumer WPML KMZ for `drone` following `spec` to `filepath`.

    Raises
    ------
    ValueError  if spec has no waypoints
    IOError     if the file cannot be written
    """
    if not spec.waypoints:
        raise ValueError('No waypoints provided — define a survey area first.')

    finish_action = _FINISH_ACTION.get(spec.finish_action, 'goHome')
    height_mode   = 'relativeToStartPoint'
    exit_on_rc_lost, rc_lost_action = _RC_LOST_ACTION.get(
        spec.rc_lost_action, ('executeLostAction', 'goBack')
    )
    ts_ms = int(spec.create_time_ms) if spec.create_time_ms else int(time.time() * 1000)

    mission_config = _mission_config_xml(drone.drone_enum, finish_action,
                                         spec.speed_ms, exit_on_rc_lost, rc_lost_action)
    template_kml   = _build_template_kml(mission_config, ts_ms, spec.mission_name,
                                         spec.speed_ms, spec.altitude_m, height_mode)
    if spec.heights is not None and len(spec.heights) != len(spec.waypoints):
        raise ValueError('Terrain heights do not match the waypoints.')
    waylines_wpml  = _build_waylines_wpml(
        spec.waypoints, spec.altitude_m, spec.speed_ms, height_mode,
        spec.gimbal_pitch, mission_config, spec.capture_mode, spec.heights
    )

    package_kmz(filepath, [
        ('wpmz/template.kml',  template_kml),
        ('wpmz/waylines.wpml', waylines_wpml),
    ])


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
    <wpml:author>{esc(mission_name)}</wpml:author>
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
                          gimbal_pitch, mission_config, capture_mode='semi',
                          heights=None):
    """waylines.wpml — repeats missionConfig + full Placemark list.

    In 'full' capture mode every waypoint also carries a takePhoto action, so
    the drone shoots automatically at each photo location (full-automatic 2D
    mapping). When `heights` is given (terrain follow) each waypoint uses its own
    executeHeight instead of the single altitude; heightMode stays relative to
    the launch point."""
    placemark_blocks = []
    group_id = 1                                     # unique per action group

    for idx, (lon, lat) in enumerate(waypoints):
        action_groups = ''
        if idx == 0:
            action_groups += _gimbal_action_group(group_id=group_id,
                                                  pitch_angle=gimbal_pitch)
            group_id += 1
        if capture_mode == 'full':
            action_groups += _take_photo_action_group(group_id=group_id, index=idx)
            group_id += 1
        wp_height = heights[idx] if heights is not None else altitude_m
        placemark_blocks.append(
            _placemark(idx, lon, lat, wp_height, speed_ms,
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
          <!-- Straight legs with a full stop at each point. Discontinuity (not
               continuity) curvature keeps mapping lines straight instead of
               bowing/rounding, especially at the line-end turnarounds.
               (Fix contributed by CallumGreenwald, PR #7; tested on a Mini 5 Pro.) -->
          <wpml:waypointTurnMode>toPointAndStopWithDiscontinuityCurvature</wpml:waypointTurnMode>
          <wpml:waypointTurnDampingDist>0</wpml:waypointTurnDampingDist>
        </wpml:waypointTurnParam>
        <wpml:useStraightLine>1</wpml:useStraightLine>
{action_groups_xml}        <wpml:waypointGimbalHeadingParam>
          <wpml:waypointGimbalPitchAngle>{gimbal_pitch}</wpml:waypointGimbalPitchAngle>
          <wpml:waypointGimbalYawAngle>0</wpml:waypointGimbalYawAngle>
        </wpml:waypointGimbalHeadingParam>
      </Placemark>'''


def _take_photo_action_group(group_id, index):
    """Take one photo on reaching this waypoint (full-automatic capture)."""
    return f'''        <wpml:actionGroup>
          <wpml:actionGroupId>{group_id}</wpml:actionGroupId>
          <wpml:actionGroupStartIndex>{index}</wpml:actionGroupStartIndex>
          <wpml:actionGroupEndIndex>{index}</wpml:actionGroupEndIndex>
          <wpml:actionGroupMode>parallel</wpml:actionGroupMode>
          <wpml:actionTrigger>
            <wpml:actionTriggerType>reachPoint</wpml:actionTriggerType>
          </wpml:actionTrigger>
          <wpml:action>
            <wpml:actionId>{group_id}</wpml:actionId>
            <wpml:actionActuatorFunc>takePhoto</wpml:actionActuatorFunc>
            <wpml:actionActuatorFuncParam>
              <wpml:payloadPositionIndex>0</wpml:payloadPositionIndex>
            </wpml:actionActuatorFuncParam>
          </wpml:action>
        </wpml:actionGroup>
'''


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
