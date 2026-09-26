"""QGIS layers used to display a generated FlyPath route."""

from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis, QgsFeature, QgsGeometry, QgsLineSymbol, QgsMarkerSymbol,
    QgsLayerTreeGroup, QgsLayerTreeLayer, QgsPalLayerSettings, QgsPointXY, QgsProject,
    QgsProperty, QgsRuleBasedRenderer, QgsSimpleLineSymbolLayer, QgsSymbolLayer,
    QgsTextFormat, QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)


START_COLOR = '#69B7FF'
END_COLOR = '#FF8A78'
MID_COLOR = '#050C14'
_FLIGHT_COLORS = ['#FFE600', '#ff9f43', '#a88bff', '#35c99a', '#ff6f91']
_ORDER = ('waypoints', 'breaks', 'path', 'survey', 'corridor',
          'takeoff', 'contours')
try:
    _FONT_BOLD = QFont.Weight.Bold
except AttributeError:
    _FONT_BOLD = getattr(QFont, 'Bold')
try:
    _SYMBOL_OFFSET = QgsSymbolLayer.Property.PropertyOffset
    _LABEL_OFFSET = QgsPalLayerSettings.Property.OffsetXY
except AttributeError:
    _SYMBOL_OFFSET = QgsSymbolLayer.PropertyOffset
    _LABEL_OFFSET = QgsPalLayerSettings.OffsetXY


def create(missions, heights=None, ground=None, project=None):
    """Create and register the path and waypoint layers; return their IDs."""
    project = project or QgsProject.instance()
    path = _path_layer(missions)
    waypoints = _waypoint_layer(missions, heights, ground)
    register(path, project, kind='path')
    register(waypoints, project, kind='waypoints')
    waypoints_id = waypoints.id()
    path.repaintRequested.connect(
        lambda: _sync_waypoint_flights(path, project.mapLayer(waypoints_id)))
    return [path.id(), waypoints.id()]


def _sync_waypoint_flights(path, waypoints):
    """Follow the checked flight rules without changing waypoint categories."""
    if waypoints is None:
        return
    renderer = path.renderer()
    if not isinstance(renderer, QgsRuleBasedRenderer):
        waypoints.setCustomProperty('flypath_flight_visibility', None)
        if waypoints.subsetString():
            waypoints.setSubsetString('')
        return
    rules = renderer.rootRule().children()
    checked = [index for index, rule in enumerate(rules) if rule.active()]
    subset = ('' if len(checked) == len(rules) else
              f'"mission" IN ({", ".join(map(str, checked))})' if checked else
              '"mission" = -1')
    waypoints.setCustomProperty('flypath_flight_visibility',
                                [rule.active() for rule in rules])
    if waypoints.subsetString() != subset:
        waypoints.setSubsetString(subset)


def register(layer, project=None, kind=None):
    """Place a temporary FlyPath layer in the plugin-owned root group."""
    project = project or QgsProject.instance()
    group = _group(project)
    if kind:
        layer.setCustomProperty('flypath_kind', kind)
    project.addMapLayer(layer, False)
    rank = _rank(kind)
    index = next((i for i, node in enumerate(group.children())
                  if isinstance(node, QgsLayerTreeLayer) and node.layer() and
                  _rank(node.layer().customProperty('flypath_kind')) > rank),
                 len(group.children()))
    group.insertLayer(index, layer)


def _rank(kind):
    return _ORDER.index(kind) if kind in _ORDER else len(_ORDER)


def _owned_group(project):
    """Find the owned group without moving it or creating another."""
    groups = list(project.layerTreeRoot().children())
    while groups:
        node = groups.pop()
        if isinstance(node, QgsLayerTreeGroup):
            if node.customProperty('flypath_group') is True:
                return node
            groups.extend(node.children())
    return None


def visibility(project=None):
    """Capture checked states before replacing temporary layers."""
    project = project or QgsProject.instance()
    group = _owned_group(project)
    if group is None:
        return None
    layers = {node.layer().customProperty('flypath_kind'): node.layer()
              for node in group.children()
              if isinstance(node, QgsLayerTreeLayer) and node.layer()}
    path = layers.get('path')
    points = layers.get('waypoints')
    if path and isinstance(path.renderer(), QgsRuleBasedRenderer):
        flights = [rule.active() for rule in path.renderer().rootRule().children()]
    else:
        flights = (points.customProperty('flypath_flight_visibility')
                   if points else None)
    return (group.itemVisibilityChecked(), {
        node.layer().customProperty('flypath_kind'): node.itemVisibilityChecked()
        for node in group.children()
        if isinstance(node, QgsLayerTreeLayer) and node.layer() and
        node.layer().customProperty('flypath_kind') in _ORDER}, flights)


def restore_visibility(saved, project=None):
    if saved is None:
        return
    project = project or QgsProject.instance()
    group = _owned_group(project)
    if group is None:
        return
    group.setItemVisibilityChecked(saved[0])
    for node in group.children():
        kind = (node.layer().customProperty('flypath_kind')
                if isinstance(node, QgsLayerTreeLayer) and node.layer() else None)
        if kind in _ORDER and kind in saved[1]:
            node.setItemVisibilityChecked(saved[1][kind])
    path = next((node.layer() for node in group.children()
                 if isinstance(node, QgsLayerTreeLayer) and node.layer() and
                 node.layer().customProperty('flypath_kind') == 'path'), None)
    if path and isinstance(path.renderer(), QgsRuleBasedRenderer) and saved[2] is not None:
        for rule, checked in zip(path.renderer().rootRule().children(), saved[2]):
            path.renderer().checkLegendSymbolItem(rule.ruleKey(), checked)
        path.triggerRepaint()


def _group(project):
    """Reuse the owned group and keep it above other project layers."""
    root = project.layerTreeRoot()
    group = _owned_group(project)
    if group is None:
        group = root.insertGroup(0, 'FlyPath')
        group.setCustomProperty('flypath_group', True)
    elif root.children()[0] != group:
        moved = group.clone()
        root.insertChildNode(0, moved)
        group.parent().removeChildNode(group)
        group = moved
    return group


def redraw(layer_ids, missions, heights=None, ground=None, project=None):
    """Update registered layers in place, rebuilding externally removed layers."""
    project = project or QgsProject.instance()
    path = project.mapLayer(layer_ids[0]) if len(layer_ids) >= 1 else None
    waypoints = project.mapLayer(layer_ids[1]) if len(layer_ids) >= 2 else None
    if path is None or waypoints is None:
        saved = visibility(project)
        remove(layer_ids, project)
        ids = create(missions, heights, ground, project)
        restore_visibility(saved, project)
        return ids
    _group(project)
    saved = visibility(project)
    path.setRenderer(_path_renderer(len(missions)))
    _populate_path(path, missions)
    _populate_waypoints(waypoints, missions, heights, ground)
    restore_visibility(saved, project)
    return layer_ids


def remove(layer_ids, project=None):
    """Remove all registered preview layers, tolerating outside removal."""
    project = project or QgsProject.instance()
    for layer_id in layer_ids:
        if project.mapLayer(layer_id):
            project.removeMapLayer(layer_id)
    _remove_empty_group(project)


def remove_stale(project=None):
    """Remove FlyPath's temporary layers left in a reopened QGIS project."""
    project = project or QgsProject.instance()
    for layer_id, layer in list(project.mapLayers().items()):
        if layer.customProperty('flypath_internal'):
            project.removeMapLayer(layer_id)
    _remove_empty_group(project)


def _remove_empty_group(project):
    group = _owned_group(project)
    if group is not None and not group.children():
        group.parent().removeChildNode(group)


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
        '&field=flight_height_m:double&field=display_offset:string(10)',
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
        symbol.symbolLayer(0).setDataDefinedProperty(
            _SYMBOL_OFFSET, QgsProperty.fromField('display_offset'))
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
    labels.dataDefinedProperties().setProperty(
        _LABEL_OFFSET, QgsProperty.fromField('display_offset'))
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
            shared_end = (index == last and mission + 1 < len(missions) and
                          missions[mission + 1] and
                          points[index] == missions[mission + 1][0])
            shared_start = (index == 0 and mission > 0 and
                            missions[mission - 1] and
                            points[index] == missions[mission - 1][-1])
            offset = '-4,0' if shared_end else '4,0' if shared_start else '0,0'
            feature.setAttributes(
                [index + 1, waypoint_type, mission, elevation, height, offset])
            features.append(feature)
    provider.addFeatures(features)
    layer.triggerRepaint()
