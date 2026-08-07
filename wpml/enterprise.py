"""
wpml/enterprise.py
------------------
Mission writer for DJI enterprise drones with an integrated mapping camera
(e.g. Matrice 4E), flown from DJI Pilot 2.

Native WPML mapping2d format, namespace http://www.dji.com/wpmz/1.0.6. Emits
wpmz/template.kml (survey polygon + planned overlaps, from which Pilot 2 rebuilds
the grid) and wpmz/waylines.wpml (the flight-line turn points). Structure is
modeled on a real M4E native mission.

Note: the enterprise format and the Matrice camera/enum values still need
on-hardware verification with an actual M4E + DJI Pilot 2.
"""

import time

from .base import package_kmz

_NS = 'http://www.dji.com/wpmz/1.0.6'

_FINISH_ACTION = {
    'Return to Home':         'goHome',
    'Hover in place':         'hover',
    'Land at last waypoint':  'autoLand',
}

_RC_LOST_ACTION = {
    'Return to Home':   ('executeLostAction', 'goBack'),
    'Hover in place':   ('executeLostAction', 'hover'),
    'Land immediately': ('executeLostAction', 'landing'),
    'Continue mission': ('goContinue',        'goBack'),
}


def write(drone, spec, filepath):
    """Write an enterprise mapping2d KMZ for `drone` following `spec`.

    Raises
    ------
    ValueError  if spec has no survey polygon or no waypoints
    """
    if not spec.polygon:
        raise ValueError('Enterprise mapping missions need a survey polygon.')
    if not spec.waypoints:
        raise ValueError('No waypoints provided — define a survey area first.')

    ts_ms = int(spec.create_time_ms) if spec.create_time_ms else int(time.time() * 1000)
    cam = drone.camera

    # Photo interval Pilot 2 uses along-track, from the camera footprint and the
    # planned front overlap.
    footprint_along = cam.footprint_along(spec.altitude_m)
    photo_spacing = footprint_along * (1.0 - spec.front_overlap)
    shoot_interval = max(round(photo_spacing / spec.speed_ms, 1), 0.5) \
        if spec.speed_ms > 0 else 0.5

    front_pct = int(round(spec.front_overlap * 100))
    side_pct = int(round(spec.side_overlap * 100))

    mission_config = _mission_config_xml(drone, spec)
    template = _template_kml(drone, spec, mission_config, ts_ms, front_pct, side_pct)
    waylines = _waylines_wpml(drone, spec, mission_config, shoot_interval)

    package_kmz(filepath, [
        ('wpmz/template.kml',  template),
        ('wpmz/waylines.wpml', waylines),
    ])


def _mission_config_xml(drone, spec):
    cam = drone.camera
    finish_action = _FINISH_ACTION.get(spec.finish_action, 'goHome')
    exit_on_rc_lost, rc_lost_action = _RC_LOST_ACTION.get(
        spec.rc_lost_action, ('executeLostAction', 'goBack'))
    return f'''    <wpml:missionConfig>
      <wpml:flyToWaylineMode>safely</wpml:flyToWaylineMode>
      <wpml:finishAction>{finish_action}</wpml:finishAction>
      <wpml:exitOnRCLost>{exit_on_rc_lost}</wpml:exitOnRCLost>
      <wpml:executeRCLostAction>{rc_lost_action}</wpml:executeRCLostAction>
      <wpml:takeOffSecurityHeight>40</wpml:takeOffSecurityHeight>
      <wpml:globalTransitionalSpeed>{spec.speed_ms:.0f}</wpml:globalTransitionalSpeed>
      <wpml:droneInfo>
        <wpml:droneEnumValue>{drone.drone_enum}</wpml:droneEnumValue>
        <wpml:droneSubEnumValue>{drone.drone_sub_enum}</wpml:droneSubEnumValue>
      </wpml:droneInfo>
      <wpml:waylineAvoidLimitAreaMode>1</wpml:waylineAvoidLimitAreaMode>
      <wpml:payloadInfo>
        <wpml:payloadEnumValue>{cam.payload_enum}</wpml:payloadEnumValue>
        <wpml:payloadSubEnumValue>{cam.payload_sub_enum}</wpml:payloadSubEnumValue>
        <wpml:payloadPositionIndex>{cam.payload_position_index}</wpml:payloadPositionIndex>
      </wpml:payloadInfo>
    </wpml:missionConfig>'''


def _template_kml(drone, spec, mission_config, ts_ms, front_pct, side_pct):
    cam = drone.camera
    coords = '\n'.join(f'                {lon:.9f},{lat:.9f},0'
                       for lon, lat in spec.polygon)
    alt = spec.altitude_m
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="{_NS}">
  <Document>
    <wpml:createTime>{ts_ms}</wpml:createTime>
    <wpml:updateTime>{ts_ms}</wpml:updateTime>
{mission_config}
    <Folder>
      <wpml:templateType>mapping2d</wpml:templateType>
      <wpml:templateId>0</wpml:templateId>
      <wpml:waylineCoordinateSysParam>
        <wpml:coordinateMode>WGS84</wpml:coordinateMode>
        <wpml:heightMode>relativeToStartPoint</wpml:heightMode>
        <wpml:globalShootHeight>{alt:.0f}</wpml:globalShootHeight>
      </wpml:waylineCoordinateSysParam>
      <wpml:autoFlightSpeed>{spec.speed_ms:.0f}</wpml:autoFlightSpeed>
      <Placemark>
        <wpml:caliFlightEnable>0</wpml:caliFlightEnable>
        <wpml:elevationOptimizeEnable>1</wpml:elevationOptimizeEnable>
        <wpml:smartObliqueEnable>0</wpml:smartObliqueEnable>
        <wpml:quickOrthoMappingEnable>0</wpml:quickOrthoMappingEnable>
        <wpml:facadeWaylineEnable>0</wpml:facadeWaylineEnable>
        <wpml:isLookAtSceneSet>0</wpml:isLookAtSceneSet>
        <wpml:smartObliqueGimbalPitch>-45</wpml:smartObliqueGimbalPitch>
        <wpml:shootType>time</wpml:shootType>
        <wpml:direction>{spec.direction_deg:.0f}</wpml:direction>
        <wpml:margin>{spec.margin_m:.0f}</wpml:margin>
        <wpml:efficiencyFlightModeEnable>0</wpml:efficiencyFlightModeEnable>
        <wpml:overlap>
          <wpml:orthoLidarOverlapH>{front_pct}</wpml:orthoLidarOverlapH>
          <wpml:orthoLidarOverlapW>{side_pct}</wpml:orthoLidarOverlapW>
          <wpml:orthoCameraOverlapH>{front_pct}</wpml:orthoCameraOverlapH>
          <wpml:orthoCameraOverlapW>{side_pct}</wpml:orthoCameraOverlapW>
        </wpml:overlap>
        <Polygon>
          <outerBoundaryIs>
            <LinearRing>
              <coordinates>
{coords}
              </coordinates>
            </LinearRing>
          </outerBoundaryIs>
        </Polygon>
        <wpml:ellipsoidHeight>{alt:.0f}</wpml:ellipsoidHeight>
        <wpml:height>{alt:.0f}</wpml:height>
      </Placemark>
      <wpml:payloadParam>
        <wpml:payloadPositionIndex>{cam.payload_position_index}</wpml:payloadPositionIndex>
        <wpml:dewarpingEnable>0</wpml:dewarpingEnable>
        <wpml:returnMode>singleReturnFirst</wpml:returnMode>
        <wpml:samplingRate>240000</wpml:samplingRate>
        <wpml:scanningMode>nonRepetitive</wpml:scanningMode>
        <wpml:modelColoringEnable>0</wpml:modelColoringEnable>
        <wpml:imageFormat>visable</wpml:imageFormat>
      </wpml:payloadParam>
    </Folder>
  </Document>
</kml>
'''


def _start_action_group(cam, gimbal_pitch):
    return f'''      <wpml:startActionGroup>
        <wpml:action>
          <wpml:actionId>0</wpml:actionId>
          <wpml:actionActuatorFunc>gimbalRotate</wpml:actionActuatorFunc>
          <wpml:actionActuatorFuncParam>
            <wpml:gimbalHeadingYawBase>aircraft</wpml:gimbalHeadingYawBase>
            <wpml:gimbalRotateMode>absoluteAngle</wpml:gimbalRotateMode>
            <wpml:gimbalPitchRotateEnable>1</wpml:gimbalPitchRotateEnable>
            <wpml:gimbalPitchRotateAngle>{gimbal_pitch:.0f}</wpml:gimbalPitchRotateAngle>
            <wpml:gimbalRollRotateEnable>0</wpml:gimbalRollRotateEnable>
            <wpml:gimbalRollRotateAngle>0</wpml:gimbalRollRotateAngle>
            <wpml:gimbalYawRotateEnable>1</wpml:gimbalYawRotateEnable>
            <wpml:gimbalYawRotateAngle>0</wpml:gimbalYawRotateAngle>
            <wpml:gimbalRotateTimeEnable>0</wpml:gimbalRotateTimeEnable>
            <wpml:gimbalRotateTime>10</wpml:gimbalRotateTime>
            <wpml:payloadPositionIndex>{cam.payload_position_index}</wpml:payloadPositionIndex>
          </wpml:actionActuatorFuncParam>
        </wpml:action>
        <wpml:action>
          <wpml:actionId>1</wpml:actionId>
          <wpml:actionActuatorFunc>hover</wpml:actionActuatorFunc>
          <wpml:actionActuatorFuncParam>
            <wpml:hoverTime>0.5</wpml:hoverTime>
          </wpml:actionActuatorFuncParam>
        </wpml:action>
        <wpml:action>
          <wpml:actionId>2</wpml:actionId>
          <wpml:actionActuatorFunc>setFocusType</wpml:actionActuatorFunc>
          <wpml:actionActuatorFuncParam>
            <wpml:cameraFocusType>manual</wpml:cameraFocusType>
            <wpml:payloadPositionIndex>{cam.payload_position_index}</wpml:payloadPositionIndex>
          </wpml:actionActuatorFuncParam>
        </wpml:action>
        <wpml:action>
          <wpml:actionId>3</wpml:actionId>
          <wpml:actionActuatorFunc>focus</wpml:actionActuatorFunc>
          <wpml:actionActuatorFuncParam>
            <wpml:focusX>0</wpml:focusX>
            <wpml:focusY>0</wpml:focusY>
            <wpml:focusRegionWidth>0</wpml:focusRegionWidth>
            <wpml:focusRegionHeight>0</wpml:focusRegionHeight>
            <wpml:isPointFocus>0</wpml:isPointFocus>
            <wpml:isInfiniteFocus>1</wpml:isInfiniteFocus>
            <wpml:payloadPositionIndex>{cam.payload_position_index}</wpml:payloadPositionIndex>
            <wpml:isCalibrationFocus>0</wpml:isCalibrationFocus>
          </wpml:actionActuatorFuncParam>
        </wpml:action>
      </wpml:startActionGroup>'''


def _photo_action_group(cam, last_index, shoot_interval):
    return f'''        <wpml:actionGroup>
          <wpml:actionGroupId>0</wpml:actionGroupId>
          <wpml:actionGroupStartIndex>0</wpml:actionGroupStartIndex>
          <wpml:actionGroupEndIndex>{last_index}</wpml:actionGroupEndIndex>
          <wpml:actionGroupMode>sequence</wpml:actionGroupMode>
          <wpml:actionTrigger>
            <wpml:actionTriggerType>betweenAdjacentPoints</wpml:actionTriggerType>
          </wpml:actionTrigger>
          <wpml:action>
            <wpml:actionId>0</wpml:actionId>
            <wpml:actionActuatorFunc>gimbalAngleLock</wpml:actionActuatorFunc>
            <wpml:actionActuatorFuncParam>
              <wpml:payloadPositionIndex>{cam.payload_position_index}</wpml:payloadPositionIndex>
            </wpml:actionActuatorFuncParam>
          </wpml:action>
          <wpml:action>
            <wpml:actionId>1</wpml:actionId>
            <wpml:actionActuatorFunc>startTimeLapse</wpml:actionActuatorFunc>
            <wpml:actionActuatorFuncParam>
              <wpml:payloadPositionIndex>{cam.payload_position_index}</wpml:payloadPositionIndex>
              <wpml:useGlobalPayloadLensIndex>0</wpml:useGlobalPayloadLensIndex>
              <wpml:payloadLensIndex>visable</wpml:payloadLensIndex>
              <wpml:minShootInterval>{shoot_interval}</wpml:minShootInterval>
            </wpml:actionActuatorFuncParam>
          </wpml:action>
        </wpml:actionGroup>'''


def _placemark(idx, lon, lat, alt, speed, action_groups_xml):
    return f'''      <Placemark>
        <Point>
          <coordinates>{lon:.12f},{lat:.12f}</coordinates>
        </Point>
        <wpml:index>{idx}</wpml:index>
        <wpml:executeHeight>{alt:.0f}</wpml:executeHeight>
        <wpml:waypointSpeed>{speed:.0f}</wpml:waypointSpeed>
        <wpml:waypointHeadingParam>
          <wpml:waypointHeadingMode>followWayline</wpml:waypointHeadingMode>
          <wpml:waypointHeadingAngle>0</wpml:waypointHeadingAngle>
          <wpml:waypointPoiPoint>0.000000,0.000000,0.000000</wpml:waypointPoiPoint>
          <wpml:waypointHeadingAngleEnable>0</wpml:waypointHeadingAngleEnable>
          <wpml:waypointHeadingPathMode>followBadArc</wpml:waypointHeadingPathMode>
          <wpml:waypointHeadingPoiIndex>0</wpml:waypointHeadingPoiIndex>
        </wpml:waypointHeadingParam>
        <wpml:waypointTurnParam>
          <wpml:waypointTurnMode>toPointAndStopWithDiscontinuityCurvature</wpml:waypointTurnMode>
          <wpml:waypointTurnDampingDist>0</wpml:waypointTurnDampingDist>
        </wpml:waypointTurnParam>
        <wpml:useStraightLine>1</wpml:useStraightLine>{action_groups_xml}
        <wpml:waypointGimbalHeadingParam>
          <wpml:waypointGimbalPitchAngle>0</wpml:waypointGimbalPitchAngle>
          <wpml:waypointGimbalYawAngle>0</wpml:waypointGimbalYawAngle>
        </wpml:waypointGimbalHeadingParam>
        <wpml:isRisky>0</wpml:isRisky>
        <wpml:waypointWorkType>0</wpml:waypointWorkType>
      </Placemark>'''


def _waylines_wpml(drone, spec, mission_config, shoot_interval):
    cam = drone.camera
    pts = spec.waypoints
    last = len(pts) - 1
    photo_group = _photo_action_group(cam, last, shoot_interval)
    blocks = []
    for i, (lon, lat) in enumerate(pts):
        ag = ('\n' + photo_group) if i == 0 else ''
        blocks.append(_placemark(i, lon, lat, spec.altitude_m, spec.speed_ms, ag))
    marks = '\n'.join(blocks)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="{_NS}">
  <Document>
{mission_config}
    <Folder>
      <wpml:templateId>0</wpml:templateId>
      <wpml:executeHeightMode>relativeToStartPoint</wpml:executeHeightMode>
      <wpml:waylineId>0</wpml:waylineId>
      <wpml:distance>0</wpml:distance>
      <wpml:duration>0</wpml:duration>
      <wpml:autoFlightSpeed>{spec.speed_ms:.0f}</wpml:autoFlightSpeed>
{_start_action_group(cam, spec.gimbal_pitch)}
      <wpml:realTimeFollowSurfaceByFov>0</wpml:realTimeFollowSurfaceByFov>
{marks}
    </Folder>
  </Document>
</kml>
'''
