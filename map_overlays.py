"""QGIS layer ownership for computed FlyPath map overlays."""

from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis, QgsFeature, QgsFillSymbol, QgsGeometry, QgsLineSymbol,
    QgsPalLayerSettings, QgsPointXY, QgsProject, QgsRuleBasedRenderer,
    QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
)


_TAKEOFF_PURPLES = [
    (74, 20, 140), (123, 31, 162), (156, 39, 176), (103, 58, 183),
    (171, 71, 188), (63, 81, 181), (186, 104, 200), (149, 117, 205),
]


def create_takeoff(result, preview_layer_ids=(), project=None):
    """Build and register takeoff regions, or return None when all are empty."""
    layer = QgsVectorLayer(
        'Polygon?crs=EPSG:4326&field=mission:integer'
        '&field=ref_elevation_m:double',
        'FlyPath — Takeoff Zone', 'memory')
    layer.setCustomProperty('flypath_internal', True)
    radius = result['radius_m']
    features = []
    indices = []
    for zone in result['zones']:
        cx, cy = zone['center_utm']
        disk = QgsGeometry.fromPointXY(QgsPointXY(cx, cy)).buffer(radius, 48)
        if zone['flat']:
            region = disk
        else:
            if not zone['cells_utm']:
                continue
            spacing = zone['spacing']
            blob = QgsGeometry.unaryUnion([
                QgsGeometry.fromPointXY(QgsPointXY(x, y)).buffer(spacing, 8)
                for x, y in zone['cells_utm']
            ])
            region = blob.intersection(disk)
            if region.isEmpty():
                continue
        region.transform(result['to_wgs'])
        feature = QgsFeature()
        feature.setGeometry(region)
        feature.setAttributes([zone['index'], round(zone['ref_elev'], 1)])
        features.append(feature)
        indices.append(zone['index'])
    if not features:
        return None
    layer.dataProvider().addFeatures(features)
    layer.setRenderer(_takeoff_renderer(indices))
    layer.triggerRepaint()
    _register_below_preview(layer, preview_layer_ids, project)
    return layer


def create_contours(result, preview_layer_ids=(), project=None):
    """Build and register a labelled contour layer, or None without segments."""
    if not result['segments']:
        return None
    by_level = {}
    for level, segment in result['segments']:
        by_level.setdefault(level, []).append(
            [QgsPointXY(*segment[0]), QgsPointXY(*segment[1])])
    layer = QgsVectorLayer(
        'LineString?crs=EPSG:4326&field=level:double',
        'FlyPath — DEM Contours', 'memory')
    layer.setCustomProperty('flypath_internal', True)
    features = []
    for level, parts in by_level.items():
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromMultiPolylineXY(parts))
        feature.setAttributes([round(level, 2)])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.renderer().setSymbol(QgsLineSymbol.createSimple({
        'color': '140,90,30,180', 'width': '0.25'}))
    labels = QgsPalLayerSettings()
    labels.fieldName = 'level'
    try:
        labels.placement = Qgis.LabelPlacement.Line
    except AttributeError:
        labels.placement = getattr(QgsPalLayerSettings, 'Line')
    text = QgsTextFormat()
    text.setFont(QFont('Segoe UI', 6))
    text.setColor(QColor('#5A3C1E'))
    text.setSize(6)
    labels.setFormat(text)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(labels))
    layer.setLabelsEnabled(True)
    _register_below_preview(layer, preview_layer_ids, project)
    return layer


def remove(layer_id, project=None):
    """Remove a registered overlay and tolerate prior outside removal."""
    if not layer_id:
        return
    project = project or QgsProject.instance()
    if project.mapLayer(layer_id):
        project.removeMapLayer(layer_id)


def _takeoff_renderer(indices):
    root = QgsRuleBasedRenderer.Rule(None)
    single = len(indices) <= 1
    for mission in indices:
        red, green, blue = _TAKEOFF_PURPLES[mission % len(_TAKEOFF_PURPLES)]
        symbol = QgsFillSymbol.createSimple({
            'color': f'{red},{green},{blue},120',
            'outline_color': f'#{red:02X}{green:02X}{blue:02X}',
            'outline_width': '0.5',
        })
        label = 'Takeoff zone' if single else f'Takeoff {mission + 1}'
        root.appendChild(QgsRuleBasedRenderer.Rule(
            symbol, filterExp=f'"mission" = {mission}', label=label))
    return QgsRuleBasedRenderer(root)


def _register_below_preview(layer, preview_layer_ids, project=None):
    project = project or QgsProject.instance()
    project.addMapLayer(layer, False)
    root = project.layerTreeRoot()
    own_ids = set(preview_layer_ids)
    insert_at = 0
    for index, node in enumerate(root.children()):
        if getattr(node, 'layerId', lambda: None)() in own_ids:
            insert_at = index + 1
    root.insertLayer(insert_at, layer)
