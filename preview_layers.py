"""QGIS layers used to display a generated FlyPath route."""

from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis, QgsFeature, QgsGeometry, QgsLineSymbol, QgsMarkerSymbol,
    QgsPalLayerSettings, QgsPointXY, QgsProject, QgsRuleBasedRenderer,
    QgsSimpleLineSymbolLayer, QgsTextFormat, QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)


START_COLOR = '#69B7FF'
END_COLOR = '#FF8A78'
MID_COLOR = '#050C14'
_FLIGHT_COLORS = ['#FFE600', '#ff9f43', '#a88bff', '#35c99a', '#ff6f91']
try:
    _FONT_BOLD = QFont.Weight.Bold
except AttributeError:
    _FONT_BOLD = getattr(QFont, 'Bold')


def create(missions, heights=None, ground=None, project=None):
    """Create and register the path and waypoint layers; return their IDs."""
    project = project or QgsProject.instance()
    path = _path_layer(missions)
    waypoints = _waypoint_layer(missions, heights, ground)
    project.addMapLayer(path)
    project.addMapLayer(waypoints)
    return [path.id(), waypoints.id()]


def redraw(layer_ids, missions, heights=None, ground=None, project=None):
    """Update registered layers in place, rebuilding externally removed layers."""
    project = project or QgsProject.instance()
    path = project.mapLayer(layer_ids[0]) if len(layer_ids) >= 1 else None
    waypoints = project.mapLayer(layer_ids[1]) if len(layer_ids) >= 2 else None
    if path is None or waypoints is None:
        remove(layer_ids, project)
        return create(missions, heights, ground, project)
    path.setRenderer(_path_renderer(len(missions)))
    _populate_path(path, missions)
    _populate_waypoints(waypoints, missions, heights, ground)
    return layer_ids


def remove(layer_ids, project=None):
    """Remove all registered preview layers, tolerating outside removal."""
    project = project or QgsProject.instance()
    for layer_id in layer_ids:
        if project.mapLayer(layer_id):
            project.removeMapLayer(layer_id)


def remove_stale(project=None):
    """Remove FlyPath's temporary layers left in a reopened QGIS project."""
    project = project or QgsProject.instance()
    for layer_id, layer in list(project.mapLayers().items()):
        if layer.customProperty('flypath_internal'):
            project.removeMapLayer(layer_id)


def _path_renderer(count):
    root = QgsRuleBasedRenderer.Rule(None)
    for mission in range(max(1, count)):
        symbol = QgsLineSymbol.createSimple({
            'color': '#071018', 'width': '1.3',
            'capstyle': 'round', 'joinstyle': 'round',
        })
        symbol.appendSymbolLayer(QgsSimpleLineSymbolLayer.create({
            'color': _FLIGHT_COLORS[mission % len(_FLIGHT_COLORS)],
            'width': '0.8', 'capstyle': 'round', 'joinstyle': 'round',
        }))
        label = 'Flight path' if count <= 1 else f'Flight {mission + 1}'
        root.appendChild(QgsRuleBasedRenderer.Rule(
            symbol, filterExp=f'"mission" = {mission}', label=label))
    return QgsRuleBasedRenderer(root)


def _path_layer(missions):
    layer = QgsVectorLayer(
        'LineString?crs=EPSG:4326&field=id:integer&field=mission:integer',
        'FlyPath — Path', 'memory')
    layer.setCustomProperty('flypath_internal', True)
    layer.setRenderer(_path_renderer(len(missions)))
    _populate_path(layer, missions)
    return layer


def _populate_path(layer, missions):
    provider = layer.dataProvider()
    provider.truncate()
    features = []
    for mission, points in enumerate(missions):
        if len(points) < 2:
            continue
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(lon, lat) for lon, lat in points]))
        feature.setAttributes([mission, mission])
        features.append(feature)
    provider.addFeatures(features)
    layer.triggerRepaint()


def _waypoint_layer(missions, heights=None, ground=None):
    layer = QgsVectorLayer(
        'Point?crs=EPSG:4326&field=seq:integer&field=wp_type:string(10)'
        '&field=mission:integer&field=ground_elevation_m:double'
        '&field=flight_height_m:double',
        'FlyPath — Waypoints', 'memory')
    layer.setCustomProperty('flypath_internal', True)
    _populate_waypoints(layer, missions, heights, ground)

    root = QgsRuleBasedRenderer.Rule(None)
    rules = [
        ('"wp_type" = \'start\'', START_COLOR, MID_COLOR, '7.5', 'Start'),
        ('"wp_type" = \'end\'', END_COLOR, MID_COLOR, '7.5', 'End'),
        ('"wp_type" = \'mid\'', MID_COLOR, '#6FB5FF', '4.0', 'Waypoint'),
    ]
    for expression, color, border, size, label in rules:
        symbol = QgsMarkerSymbol.createSimple({
            'name': 'circle', 'color': color, 'outline_color': border,
            'outline_width': '0.4', 'size': size,
        })
        root.appendChild(QgsRuleBasedRenderer.Rule(
            symbol, filterExp=expression, label=label))
    layer.setRenderer(QgsRuleBasedRenderer(root))

    labels = QgsPalLayerSettings()
    labels.fieldName = 'seq'
    try:
        labels.placement = Qgis.LabelPlacement.OverPoint
    except AttributeError:
        labels.placement = getattr(QgsPalLayerSettings, 'OverPoint')
    labels.priority = 10
    text = QgsTextFormat()
    text.setFont(QFont('Segoe UI', 7, _FONT_BOLD))
    text.setColor(QColor('#FFFFFF'))
    text.setSize(7)
    labels.setFormat(text)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(labels))
    layer.setLabelsEnabled(True)
    return layer


def _populate_waypoints(layer, missions, heights=None, ground=None):
    provider = layer.dataProvider()
    provider.truncate()
    features = []
    for mission, points in enumerate(missions):
        last = len(points) - 1
        mission_heights = heights[mission] if heights and mission < len(heights) else None
        mission_ground = ground[mission] if ground and mission < len(ground) else None
        for index, (lon, lat) in enumerate(points):
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
            waypoint_type = ('start' if index == 0 else
                             'end' if index == last else 'mid')
            elevation = (mission_ground[index]
                         if mission_ground and index < len(mission_ground) else None)
            height = (mission_heights[index]
                      if mission_heights and index < len(mission_heights) else None)
            feature.setAttributes(
                [index + 1, waypoint_type, mission, elevation, height])
            features.append(feature)
    provider.addFeatures(features)
    layer.triggerRepaint()
