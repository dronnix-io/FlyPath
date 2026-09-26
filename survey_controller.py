"""QGIS survey source, drawing, editing, and corridor-break lifecycle."""
import math

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .map_tools import LineDrawTool, PolygonDrawTool, VertexPickTool
from . import preview_layers
_INFO_IDLE = 'ⓘ  Hover over any field to see what it does.'

try:
    _PolygonGeometry = QgsWkbTypes.GeometryType.PolygonGeometry
    _LineGeometry = QgsWkbTypes.GeometryType.LineGeometry
except AttributeError:
    _PolygonGeometry = getattr(QgsWkbTypes, 'PolygonGeometry')
    _LineGeometry = getattr(QgsWkbTypes, 'LineGeometry')

try:
    _BAND_CAP_ROUND = Qgis.EndCapStyle.Round
    _BAND_JOIN_MITER = Qgis.JoinStyle.Miter
except AttributeError:  # pragma: no cover - older QGIS
    _BAND_CAP_ROUND = getattr(Qgis, 'EndCapStyleRound', 1)
    _BAND_JOIN_MITER = getattr(Qgis, 'JoinStyleMiter', 2)

try:
    _MB_YES = QMessageBox.StandardButton.Yes
    _MB_NO = QMessageBox.StandardButton.No
except AttributeError:
    _MB_YES = getattr(QMessageBox, 'Yes')
    _MB_NO = getattr(QMessageBox, 'No')


class SurveyLifecycleMixin:
    """Owns acquisition and map presentation of the active survey geometry."""

    def _on_set_breaks_toggled(self, checked):
        """Activate/deactivate the vertex-pick tool for placing mission breaks."""
        if checked:
            if self._survey_line is None:
                QMessageBox.information(
                    self, 'No Corridor',
                    'Draw or select a corridor centre line first, then place '
                    'mission breaks on its vertices.')
                self.setBreaksBtn.setChecked(False)
                return
            canvas = self.iface.mapCanvas()
            self._prev_map_tool = canvas.mapTool()
            self._break_tool = VertexPickTool(canvas)
            self._break_tool.set_targets(self._break_targets())   # snap to vertices
            self._break_tool.point_picked.connect(self._on_break_point_picked)
            self._break_tool.finished.connect(
                lambda: self.setBreaksBtn.setChecked(False))
            canvas.setMapTool(self._break_tool)
            # Keep the label short so it never widens the panel; the full
            # instructions go to the info bar below.
            self.setBreaksBtn.setText('Setting Breaks…')
            self._set_info(
                'Click a centre-line vertex to break the mission there; click it '
                'again to remove the break. Press Escape when done.')
        else:
            self._leave_break_tool()

    def _leave_break_tool(self):
        canvas = self.iface.mapCanvas()
        current = canvas.mapTool()
        if self._prev_map_tool is not None:
            canvas.setMapTool(self._prev_map_tool)
            self._prev_map_tool = None
        elif isinstance(current, VertexPickTool):
            canvas.unsetMapTool(current)
        self._break_tool = None
        self.setBreaksBtn.setText('Set Mission Breaks')
        self._set_info(_INFO_IDLE)

    def _corridor_line_parts(self):
        """Centre-line parts as lists of QgsPointXY, in the line's own CRS."""
        g = self._survey_line
        if g is None:
            return []
        if g.isMultipart():
            return [list(part) for part in g.asMultiPolyline()]
        return [list(g.asPolyline())]

    def _break_targets(self):
        """Interior centre-line vertices as QgsPointXY in the canvas map CRS, for
        the pick tool to snap to. Endpoints are excluded (a part already
        starts/ends a mission)."""
        if self._survey_line is None:
            return []
        to_map = QgsCoordinateTransform(
            self._survey_line_crs,
            self.iface.mapCanvas().mapSettings().destinationCrs(),
            QgsProject.instance())
        return [to_map.transform(verts[vi])
                for verts in self._corridor_line_parts()
                for vi in range(1, len(verts) - 1)]

    def _nearest_break_vertex(self, map_pt, tol_px=16):
        """Return (part_idx, vert_idx) of the centre-line vertex nearest to a map
        point, or None if none is within tol_px pixels. Endpoints of a part are
        excluded (a part already starts/ends a mission)."""
        if self._survey_line is None:
            return None
        canvas = self.iface.mapCanvas()
        to_map = QgsCoordinateTransform(self._survey_line_crs,
                                        canvas.mapSettings().destinationCrs(),
                                        QgsProject.instance())
        mupp = canvas.mapUnitsPerPixel() or 1e-9
        tol_map = tol_px * mupp
        best = None
        best_d = tol_map
        for pi, verts in enumerate(self._corridor_line_parts()):
            for vi in range(1, len(verts) - 1):        # interior vertices only
                p = to_map.transform(verts[vi])
                d = math.hypot(p.x() - map_pt.x(), p.y() - map_pt.y())
                if d <= best_d:
                    best_d = d
                    best = (pi, vi)
        return best

    def _on_break_point_picked(self, map_pt):
        hit = self._nearest_break_vertex(map_pt)
        if hit is None:
            return
        if hit in self._corridor_breaks:
            self._corridor_breaks.discard(hit)
        else:
            self._corridor_breaks.add(hit)
        self._redraw_break_markers()
        self._update_stats()      # recount missions
        self._sync_preview()      # recolour the preview if one is shown

    def _clear_breaks(self):
        """Forget all mission breaks and remove their markers."""
        self._corridor_breaks = set()
        if self._break_layer_id:
            preview_layers.remove([self._break_layer_id])
            self._break_layer_id = None

    def _redraw_break_markers(self):
        """(Re)draw the break vertices as a styled point layer."""
        saved = preview_layers.visibility()
        if self._break_layer_id:
            preview_layers.remove([self._break_layer_id])
            self._break_layer_id = None
        if not self._corridor_breaks or self._survey_line is None:
            return
        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        to_wgs = QgsCoordinateTransform(self._survey_line_crs, wgs84,
                                        QgsProject.instance())
        parts = self._corridor_line_parts()
        layer = QgsVectorLayer('Point?crs=EPSG:4326',
                               'FlyPath — Mission Breaks', 'memory')
        layer.setCustomProperty('flypath_internal', True)
        feats = []
        for (pi, vi) in self._corridor_breaks:
            if pi < len(parts) and vi < len(parts[pi]):
                p = to_wgs.transform(parts[pi][vi])
                f = QgsFeature()
                f.setGeometry(QgsGeometry.fromPointXY(p))
                feats.append(f)
        layer.dataProvider().addFeatures(feats)
        symbol = QgsMarkerSymbol.createSimple({
            'name': 'circle', 'color': '#FFD400',
            'outline_color': '#000000', 'outline_width': '0.4', 'size': '4.5',
        })
        layer.renderer().setSymbol(symbol)
        preview_layers.register(layer, kind='breaks')
        preview_layers.restore_visibility(saved)
        self._break_layer_id = layer.id()

    def _corridor_sublines(self):
        """Split the centre line into sub-lines at the break vertices and part
        boundaries. Adjacent sub-lines share the break vertex so coverage is
        continuous. Returns a list of QgsGeometry LineStrings."""
        sublines = []
        for pi, verts in enumerate(self._corridor_line_parts()):
            if len(verts) < 2:
                continue
            cuts = sorted(vi for (p, vi) in self._corridor_breaks
                          if p == pi and 0 < vi < len(verts) - 1)
            bounds = [0] + cuts + [len(verts) - 1]
            for a, b in zip(bounds, bounds[1:]):
                sub = verts[a:b + 1]                 # inclusive; shares the break
                if len(sub) >= 2:
                    sublines.append(QgsGeometry.fromPolylineXY(sub))
        return sublines

    def _on_use_qgis_selection(self):
        """
        Inspect the current QGIS selection across all non-internal layers of the
        geometry type this mission kind needs (polygon for 2D Mapping, line for
        Corridor Mapping) and adopt the single selected feature.

        Rules:
          0 selected features total → error
          > 1 selected features total → error
          exactly 1 selected feature  → sync Layer/Feature combos + survey area
        """
        corridor = self._mission_kind() == 'corridor'
        target_geom = _LineGeometry if corridor else _PolygonGeometry
        noun = 'line' if corridor else 'polygon'
        candidates = []
        for layer in QgsProject.instance().mapLayers().values():
            if (not hasattr(layer, 'wkbType') or
                    layer.customProperty('flypath_internal') or
                    QgsWkbTypes.geometryType(layer.wkbType()) !=
                    target_geom):
                continue
            for fid in layer.selectedFeatureIds():
                candidates.append((layer, fid))

        if len(candidates) == 0:
            QMessageBox.information(
                self, 'Nothing Selected',
                f'No {noun} is selected in QGIS.\n\n'
                f'Select a single {noun} with the QGIS selection tool and try again.'
            )
            return

        if len(candidates) > 1:
            QMessageBox.warning(
                self, f'Multiple {noun.capitalize()}s Selected',
                f'{len(candidates)} {noun}s are selected across one or more layers.\n\n'
                f'FlyPath supports one survey {noun} at a time.\n'
                f'Select exactly one {noun} and try again.'
            )
            return

        layer, fid = candidates[0]
        feat = next(layer.getFeatures([fid]))

        # Remove any active drawn geometry
        if self._survey_area_layer_id:
            self._on_remove_drawn_polygon()

        # Sync Layer combo
        layer_idx = self.layerCombo.findData(layer.id())
        if layer_idx < 0:
            return
        self.layerCombo.blockSignals(True)
        self.layerCombo.setCurrentIndex(layer_idx)
        self.layerCombo.blockSignals(False)

        # Connect layer edit signals if not already done
        if self._monitored_layer_id != layer.id():
            self._disconnect_layer_signals()
            self._connect_layer_signals(layer)

        # Sync the Feature combo, then point it at the selected feature
        self._populate_feature_combo(layer)
        if layer.featureCount() > 1:
            feat_idx = self.featureCombo.findData(fid)
            if feat_idx >= 0:
                self.featureCombo.blockSignals(True)
                self.featureCombo.setCurrentIndex(feat_idx)
                self.featureCombo.blockSignals(False)

        self._set_survey_geometry(feat.geometry(), layer.crs(),
                                  layer_id=layer.id(), fid=fid)
        self.selectionInfoLabel.setText('%s  ·  FID %s' % (layer.name(), fid))

    def _on_layer_changed(self):
        layer_id = self.layerCombo.currentData()
        self._disconnect_layer_signals()

        # Reset feature combo (hidden, label and all, until a layer is chosen)
        self.featureCombo.blockSignals(True)
        self.featureCombo.clear()
        self._set_feature_row_visible(False)
        self.featureCombo.blockSignals(False)

        if not layer_id:
            self._survey_polygon     = None
            self._survey_polygon_crs = None
            self._on_clear_preview(reset_area=False)
            self._clear_stats()
            return

        layer = QgsProject.instance().mapLayer(layer_id)
        if not layer:
            return

        # A layer-based polygon replaces any drawn polygon
        if self._survey_area_layer_id:
            self._remove_survey_area_layer()
            self._on_clear_preview(reset_area=False)
            self._survey_polygon     = None
            self._survey_polygon_crs = None

        self._connect_layer_signals(layer)
        self._populate_feature_combo(layer)

    def _set_feature_row_visible(self, visible):
        """Show or hide the Feature (FID) picker, with its 'Feature' caption, that
        sits beside the Layer combo. Hiding it lets the Layer combo take the row."""
        self.featureCombo.setVisible(visible)
        self._featureCaption.setVisible(visible)

    def _source_mode_value(self):
        """Which survey-area source is selected: 'layer', 'selection' or 'draw'."""
        if self.sourceDrawRadio.isChecked():
            return 'draw'
        if self.sourceSelectionRadio.isChecked():
            return 'selection'
        return 'layer'

    def _apply_source_mode(self):
        """Switch the shared source row to the chosen source's controls: the
        layer + feature combos ('layer'), the Use QGIS Selection button
        ('selection'), or the draw buttons ('draw'). One stacked row keeps them
        all at the same position and height."""
        mode = self._source_mode_value()
        self._sourceStack.setCurrentIndex(
            {'layer': 0, 'selection': 1, 'draw': 2}[mode])

    def _on_source_mode_changed(self, _=None):
        """Switching source starts from a clean slate so areas from different
        sources never mix. Clicking the already-selected source does nothing."""
        mode = self._source_mode_value()
        if mode == self._source_mode:
            return
        self._source_mode = mode
        self._apply_source_mode()
        self._on_clear_preview(reset_area=True)

    def _clear_survey_from_feature(self):
        """Drop the current survey area and its stats (no feature chosen)."""
        self._survey_polygon     = None
        self._survey_polygon_crs = None
        self._survey_line        = None
        self._survey_line_crs    = None
        self.areaLabel.setText('—')
        self._clear_stats()

    def _feature_label(self, layer, feat, name_field):
        """Human label for a feature row in the combo."""
        fid = feat.id()
        return (f'FID {fid}  ·  {feat[name_field]}'
                if name_field else f'FID {fid}')

    def _populate_feature_combo(self, layer):
        """Populate (or refresh) the feature combo for the given layer.

        The combo is always shown while a layer is selected:
          0 features  -> a single '— none —' entry, no survey area
          1 feature   -> that feature, auto-selected as the survey area
          >1 features -> a picker, prompting the user to choose one
        """
        layer_id = layer.id()

        # Remember current selection to restore it after a refresh
        prev_fid = self.featureCombo.currentData()

        self._set_feature_row_visible(True)
        self.featureCombo.blockSignals(True)
        self.featureCombo.clear()

        count = layer.featureCount()

        # 0 (or unknown-and-empty): show a None entry, no survey area
        if count == 0:
            self.featureCombo.addItem('— none —', None)
            self.featureCombo.setCurrentIndex(0)
            self.featureCombo.blockSignals(False)
            self._clear_survey_from_feature()
            return

        name_field = self._guess_name_field(layer)

        # Exactly one feature: list it and select it automatically
        if count == 1:
            feat = next(layer.getFeatures())
            self.featureCombo.addItem(self._feature_label(layer, feat, name_field),
                                      feat.id())
            self.featureCombo.setCurrentIndex(0)
            self.featureCombo.blockSignals(False)
            self._set_survey_geometry(feat.geometry(), layer.crs(),
                                      layer_id=layer_id, fid=feat.id())
            return

        # Several features: prompt the user to pick one
        self.featureCombo.addItem('— select a feature —', None)
        for feat in layer.getFeatures():
            self.featureCombo.addItem(self._feature_label(layer, feat, name_field),
                                      feat.id())

        # Restore previous selection if that feature still exists
        idx = self.featureCombo.findData(prev_fid)
        if idx >= 0:
            self.featureCombo.setCurrentIndex(idx)
            self.featureCombo.blockSignals(False)
        else:
            # Previously selected feature was deleted — reset survey area
            self.featureCombo.setCurrentIndex(0)
            self.featureCombo.blockSignals(False)
            self._clear_survey_from_feature()

    def _connect_layer_signals(self, layer):
        """Connect to a layer's edit signals to keep the feature combo in sync."""
        layer.featureAdded.connect(self._on_layer_features_changed)
        layer.featuresDeleted.connect(self._on_layer_features_changed)
        layer.editingStopped.connect(self._on_layer_features_changed)
        layer.attributeValueChanged.connect(self._on_layer_features_changed)
        self._monitored_layer_id = layer.id()

    def _disconnect_layer_signals(self):
        """Disconnect from the previously monitored layer's edit signals."""
        if not self._monitored_layer_id:
            return
        layer = QgsProject.instance().mapLayer(self._monitored_layer_id)
        if layer:
            try:
                layer.featureAdded.disconnect(self._on_layer_features_changed)
                layer.featuresDeleted.disconnect(self._on_layer_features_changed)
                layer.editingStopped.disconnect(self._on_layer_features_changed)
                layer.attributeValueChanged.disconnect(self._on_layer_features_changed)
            except RuntimeError:
                pass  # signals already disconnected — safe to ignore
        self._monitored_layer_id = None

    def _on_layer_features_changed(self, *_args):
        """Refresh the feature combo whenever features are added, deleted, or edited."""
        layer_id = self.layerCombo.currentData()
        if not layer_id:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer:
            self._populate_feature_combo(layer)

    def _on_feature_changed(self):
        fid = self.featureCombo.currentData()
        if fid is None:
            self._clear_layer_selection()
            self._survey_polygon     = None
            self._survey_polygon_crs = None
            self._survey_line        = None
            self._survey_line_crs    = None
            self._on_clear_preview(reset_area=False)
            self._clear_stats()
            return
        layer_id = self.layerCombo.currentData()
        layer    = QgsProject.instance().mapLayer(layer_id)
        if not layer:
            return
        feats = list(layer.getFeatures([fid]))
        if not feats:
            return
        self._set_survey_geometry(feats[0].geometry(), layer.crs(),
                                  layer_id=layer_id, fid=fid)

    def _set_survey_geometry(self, geom, crs, layer_id=None, fid=None):
        """Dispatch a chosen feature to the polygon (2D) or line (corridor)
        survey-area setter based on the current mission kind."""
        if self._mission_kind() == 'corridor':
            self._set_survey_line(geom, crs, layer_id=layer_id, fid=fid)
        else:
            self._set_survey_polygon(geom, crs, layer_id=layer_id, fid=fid)

    def _set_survey_line(self, geom, crs, layer_id=None, fid=None):
        """Adopt a line feature as the corridor centre line and show its length."""
        self._on_clear_preview(reset_area=False)   # clear old flight path
        self._clear_layer_selection()
        self._clear_breaks()                       # break indices belonged to the old line
        self._survey_line     = geom
        self._survey_line_crs = crs
        if layer_id and fid is not None:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer:
                self._syncing_selection = True
                layer.selectByIds([fid])
                self._syncing_selection = False
                self._selected_layer_id = layer_id
                self.iface.mapCanvas().refresh()
        # _update_stats (corridor branch) refreshes the length read-out.
        self._update_stats()

    def _corridor_length_text(self):
        """Corridor centre-line length as a friendly 'x.xx km' / 'x m' string."""
        if self._survey_line is None or self._survey_line_crs is None:
            return '—'
        da = QgsDistanceArea()
        da.setSourceCrs(self._survey_line_crs,
                        QgsProject.instance().transformContext())
        da.setEllipsoid('WGS84')
        length_m = da.measureLength(self._survey_line)
        if length_m >= 1000.0:
            return f'{length_m / 1000.0:.2f} km'
        return f'{length_m:.0f} m'

    def _set_survey_polygon(self, geom, crs, layer_id=None, fid=None):
        self._on_clear_preview(reset_area=False)   # clear old flight path
        self._clear_layer_selection()
        self._survey_polygon     = geom
        self._survey_polygon_crs = crs
        # Highlight the chosen feature in the map canvas
        if layer_id and fid is not None:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer:
                self._syncing_selection = True
                layer.selectByIds([fid])
                self._syncing_selection = False
                self._selected_layer_id = layer_id
                self.iface.mapCanvas().refresh()
        area_ha = self._area_ha()
        self.areaLabel.setText(f'{area_ha:.2f} ha')
        self._update_stats()
        self._check_area_advisory(area_ha)

    def _clear_layer_selection(self):
        if self._selected_layer_id:
            layer = QgsProject.instance().mapLayer(self._selected_layer_id)
            if layer:
                layer.removeSelection()
                self.iface.mapCanvas().refresh()
            self._selected_layer_id = None

    def _check_area_advisory(self, area_ha):
        if area_ha > 200:
            QMessageBox.information(
                self, 'Large Survey Area',
                f'The selected area is {area_ha:.0f} ha.\n\n'
                'A survey this large needs more than one battery. Use the '
                'Split Missions field in Flight Parameters to divide it into '
                'separate missions, one per battery (it defaults to the '
                'estimated Batteries count), then export and fly each part in '
                'turn.'
            )

    @staticmethod
    def _guess_name_field(layer):
        """Return the first text-like field name, or None."""
        for field in layer.fields():
            if field.type() in (QVariant.String,):
                return field.name()
        return None

    def _on_draw_polygon(self, checked):
        # The single Draw button switches tool by mission kind.
        if self._mission_kind() == 'corridor':
            self._on_draw_line(checked)
            return
        if checked:
            if self._survey_polygon is not None:
                reply = QMessageBox.question(
                    self, 'Replace Survey Area?',
                    'A survey area is already defined.\n\n'
                    'Do you want to discard it and draw a new polygon?',
                    _MB_YES | _MB_NO,
                    _MB_NO,
                )
                if reply != _MB_YES:
                    self.drawPolygonBtn.setChecked(False)
                    return
                # User confirmed — clear everything before drawing
                self._on_clear_preview(reset_area=True)

            # Reset layer / feature selection — drawn polygon is standalone
            self._disconnect_layer_signals()
            self._clear_layer_selection()
            self.layerCombo.blockSignals(True)
            self.layerCombo.setCurrentIndex(0)
            self.layerCombo.blockSignals(False)
            self.featureCombo.blockSignals(True)
            self.featureCombo.clear()
            self.featureCombo.setVisible(False)
            self.featureCombo.blockSignals(False)

            canvas = self.iface.mapCanvas()
            self._prev_map_tool = canvas.mapTool()
            self._draw_tool = PolygonDrawTool(canvas)
            self._draw_tool.polygon_completed.connect(self._on_polygon_drawn)
            self._draw_tool.drawing_cancelled.connect(self._on_drawing_cancelled)
            canvas.setMapTool(self._draw_tool)
            self.drawPolygonBtn.setText('Drawing…  (right-click or double-click to finish)')
        else:
            self._cancel_draw_tool()

    def _leave_draw_tool(self):
        """Turn off the crosshair draw tool. Restores whatever map tool was
        active before drawing, or unsets ours if there was none, so the plus
        cursor is only ever shown while Draw or Edit is active."""
        canvas = self.iface.mapCanvas()
        current = canvas.mapTool()
        if self._prev_map_tool is not None:
            canvas.setMapTool(self._prev_map_tool)
            self._prev_map_tool = None
        elif isinstance(current, (PolygonDrawTool, LineDrawTool)):
            # No earlier tool to fall back to (the canvas had none when Draw
            # started); clear ours so the cursor returns to the default arrow.
            canvas.unsetMapTool(current)
        self._draw_tool = None

    def _on_polygon_drawn(self, geom):
        self.drawPolygonBtn.setChecked(False)
        self.drawPolygonBtn.setText('Draw Polygon on Map')
        self._leave_draw_tool()
        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        self._show_drawn_polygon(geom, crs)
        self._set_survey_polygon(geom, crs)

    def _show_drawn_polygon(self, geom, crs):
        """Add the drawn survey boundary as a styled temporary layer."""
        saved = preview_layers.visibility()
        self._remove_survey_area_layer()

        # Reproject to WGS84 for consistency with other preview layers
        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        xform = QgsCoordinateTransform(crs, wgs84, QgsProject.instance())
        g = QgsGeometry(geom)
        g.transform(xform)

        layer = QgsVectorLayer('Polygon?crs=EPSG:4326',
                               'FlyPath — Survey Area', 'memory')
        layer.setCustomProperty('flypath_internal', True)
        feat = QgsFeature()
        feat.setGeometry(g)
        layer.dataProvider().addFeatures([feat])

        symbol = QgsFillSymbol.createSimple({
            'color': '255,20,147,46',
            'outline_color': '#FF1493',
            'outline_width': '0.8',
            'outline_style': 'dash',
        })
        layer.renderer().setSymbol(symbol)

        # Track geometry edits made via QGIS tools
        layer.editingStopped.connect(self._on_survey_area_edited)
        layer.geometryChanged.connect(self._on_survey_area_geometry_changed)

        preview_layers.register(layer, kind='survey')
        preview_layers.restore_visibility(saved)
        self._survey_area_layer_id = layer.id()
        self.editPolygonBtn.setText('✎ Edit')
        self.editPolygonBtn.setVisible(True)
        self.removePolygonBtn.setVisible(True)
        self.iface.mapCanvas().refresh()

    def _on_draw_line(self, checked):
        """Draw the corridor centre line (mirrors the polygon draw flow)."""
        if checked:
            if self._survey_line is not None:
                reply = QMessageBox.question(
                    self, 'Replace Corridor?',
                    'A corridor centre line is already defined.\n\n'
                    'Do you want to discard it and draw a new line?',
                    _MB_YES | _MB_NO,
                    _MB_NO,
                )
                if reply != _MB_YES:
                    self.drawPolygonBtn.setChecked(False)
                    return
                self._on_clear_preview(reset_area=True)

            # Reset layer / feature selection — a drawn line is standalone
            self._disconnect_layer_signals()
            self._clear_layer_selection()
            self.layerCombo.blockSignals(True)
            self.layerCombo.setCurrentIndex(0)
            self.layerCombo.blockSignals(False)
            self.featureCombo.blockSignals(True)
            self.featureCombo.clear()
            self._set_feature_row_visible(False)
            self.featureCombo.blockSignals(False)

            canvas = self.iface.mapCanvas()
            self._prev_map_tool = canvas.mapTool()
            self._draw_tool = LineDrawTool(canvas)
            self._draw_tool.line_completed.connect(self._on_line_drawn)
            self._draw_tool.drawing_cancelled.connect(self._on_drawing_cancelled)
            canvas.setMapTool(self._draw_tool)
            self.drawPolygonBtn.setText('Drawing…  (right-click or double-click to finish)')
        else:
            self._cancel_draw_tool()

    def _on_line_drawn(self, geom):
        self.drawPolygonBtn.setChecked(False)
        self.drawPolygonBtn.setText(self._draw_btn_default_text())
        self._leave_draw_tool()
        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        self._show_drawn_line(geom, crs)
        self._set_survey_line(geom, crs)

    def _show_drawn_line(self, geom, crs):
        """Add the drawn corridor centre line as a styled temporary layer."""
        saved = preview_layers.visibility()
        self._remove_survey_area_layer()

        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        xform = QgsCoordinateTransform(crs, wgs84, QgsProject.instance())
        g = QgsGeometry(geom)
        g.transform(xform)

        layer = QgsVectorLayer('LineString?crs=EPSG:4326',
                               'FlyPath — Corridor Centre Line', 'memory')
        layer.setCustomProperty('flypath_internal', True)
        feat = QgsFeature()
        feat.setGeometry(g)
        layer.dataProvider().addFeatures([feat])

        symbol = QgsLineSymbol.createSimple({
            'color': '#FF1493',
            'width': '0.8',
            'line_style': 'dash',
        })
        layer.renderer().setSymbol(symbol)

        # Track geometry edits made via QGIS tools (same hooks as the polygon;
        # both handlers branch on mission kind to update the right geometry).
        layer.editingStopped.connect(self._on_survey_area_edited)
        layer.geometryChanged.connect(self._on_survey_area_geometry_changed)

        preview_layers.register(layer, kind='survey')
        preview_layers.restore_visibility(saved)
        self._survey_area_layer_id = layer.id()
        self.editPolygonBtn.setText('✎ Edit')
        self.editPolygonBtn.setVisible(True)
        self.removePolygonBtn.setVisible(True)
        self.iface.mapCanvas().refresh()

    def _clear_corridor_band(self):
        """Remove the illustrative buffered-corridor overlay if present."""
        if self._corridor_band_layer_id:
            preview_layers.remove([self._corridor_band_layer_id])
            self._corridor_band_layer_id = None

    def _show_corridor_band(self):
        """Draw the assumed mapped area as a translucent band: the centre line
        buffered by the corridor half-width. Illustrative only (never exported);
        it just shows roughly what ground the passes will cover. No-op unless a
        corridor centre line is defined."""
        saved = preview_layers.visibility()
        self._clear_corridor_band()
        if (self._mission_kind() != 'corridor' or self._survey_line is None
                or self._survey_line_crs is None):
            return
        buffer_m = self.bufferSpin.value()
        if buffer_m <= 0:
            return
        # Buffer in a metric UTM CRS so the width is correct, then show in WGS84.
        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        to_wgs = QgsCoordinateTransform(self._survey_line_crs, wgs84,
                                        QgsProject.instance())
        lw = QgsGeometry(self._survey_line)
        lw.transform(to_wgs)
        c = lw.centroid().asPoint()
        zone = int((c.x() + 180.0) / 6.0) + 1
        epsg = (32600 if c.y() >= 0 else 32700) + zone
        utm = QgsCoordinateReferenceSystem(f'EPSG:{epsg}')
        to_utm = QgsCoordinateTransform(self._survey_line_crs, utm,
                                        QgsProject.instance())
        from_utm = QgsCoordinateTransform(utm, wgs84, QgsProject.instance())
        band = QgsGeometry(self._survey_line)
        band.transform(to_utm)
        # Mitred corners so the band follows the centre line's angles (not rounded).
        band = band.buffer(buffer_m, 8, _BAND_CAP_ROUND, _BAND_JOIN_MITER, 2.0)
        band.transform(from_utm)

        layer = QgsVectorLayer('Polygon?crs=EPSG:4326',
                               'FlyPath — Corridor Area (assumed)', 'memory')
        layer.setCustomProperty('flypath_internal', True)
        feat = QgsFeature()
        feat.setGeometry(band)
        layer.dataProvider().addFeatures([feat])
        symbol = QgsFillSymbol.createSimple({
            'color': '30,144,255,71',        # translucent blue fill
            'outline_color': '#1E90FF',
            'outline_width': '0.3',
            'outline_style': 'dot',
        })
        layer.renderer().setSymbol(symbol)
        preview_layers.register(layer, kind='corridor')
        preview_layers.restore_visibility(saved)
        self._corridor_band_layer_id = layer.id()

    def _on_survey_area_edited(self):
        """Called after the user commits edits to the drawn survey geometry
        (polygon in 2D Mapping, centre line in Corridor Mapping)."""
        self._finish_polygon_edit()
        if not self._survey_area_layer_id:
            return
        layer = QgsProject.instance().mapLayer(self._survey_area_layer_id)
        if not layer or layer.featureCount() == 0:
            return
        feat = next(layer.getFeatures())
        if self._mission_kind() == 'corridor':
            self._survey_line     = feat.geometry()
            self._survey_line_crs = layer.crs()
            self._clear_breaks()          # editing the line changes vertex indices
            self._on_clear_preview(reset_area=False)
            self._update_stats()          # corridor branch refreshes Length
            return
        self._survey_polygon     = feat.geometry()
        self._survey_polygon_crs = layer.crs()
        self._on_clear_preview(reset_area=False)
        area_ha = self._area_ha()
        self.areaLabel.setText(f'{area_ha:.2f} ha')
        self._update_stats()

    def _on_survey_area_geometry_changed(self, _fid, geom):
        """Update the stored geometry live as it is being edited."""
        if not self._survey_area_layer_id:
            return
        layer = QgsProject.instance().mapLayer(self._survey_area_layer_id)
        if not layer:
            return
        if self._mission_kind() == 'corridor':
            self._survey_line     = geom
            self._survey_line_crs = layer.crs()
            self._on_clear_preview(reset_area=False)
            self._update_stats()          # corridor branch refreshes Length
            return
        self._survey_polygon     = geom
        self._survey_polygon_crs = layer.crs()
        self._on_clear_preview(reset_area=False)
        self.areaLabel.setText(f'{self._area_ha():.2f} ha')
        self._update_stats()

    def _on_remove_drawn_polygon(self):
        """Remove the drawn polygon and reset the survey area."""
        # Guarantee the crosshair is gone: if a draw is somehow still active
        # (e.g. it started with no prior tool to fall back to), drop it here.
        self._leave_draw_tool()
        self._remove_survey_area_layer()
        self._on_clear_preview(reset_area=False)
        self._survey_polygon     = None
        self._survey_polygon_crs = None
        self._survey_line        = None
        self._survey_line_crs    = None
        self._clear_breaks()
        self._waypoints          = []
        self._shot_spacing_m     = 0.0
        self.removePolygonBtn.setVisible(False)
        self._clear_stats()
        self.iface.mapCanvas().refresh()

    def _remove_survey_area_layer(self):
        """Remove the temporary drawn-polygon layer if it exists."""
        if self._survey_area_layer_id:
            layer = QgsProject.instance().mapLayer(self._survey_area_layer_id)
            if layer:
                try:
                    layer.editingStopped.disconnect(self._on_survey_area_edited)
                    layer.geometryChanged.disconnect(self._on_survey_area_geometry_changed)
                except (TypeError, RuntimeError):
                    # Signals were never connected, or the layer's C++ object
                    # is already gone; nothing to disconnect.
                    pass
                # If Edit was left on, the layer has an open edit buffer, which
                # makes QGIS pop a "save changes?" prompt on removal. Discard the
                # edits and leave the vertex tool first so removal is silent.
                if layer.isEditable():
                    layer.rollBack()
                    self._finish_polygon_edit()
                preview_layers.remove([self._survey_area_layer_id])
            self._survey_area_layer_id = None
        self.editPolygonBtn.setVisible(False)
        self.editPolygonBtn.setText('✎ Edit')
        self.removePolygonBtn.setVisible(False)

    def _on_edit_polygon(self):
        """Toggle vertex editing of the drawn survey polygon with QGIS tools."""
        if not self._survey_area_layer_id:
            return
        layer = QgsProject.instance().mapLayer(self._survey_area_layer_id)
        if layer is None:
            return
        if layer.isEditable():
            # Finish: commit the edits. This fires editingStopped, which runs
            # _on_survey_area_edited to sync the polygon and reset the UI.
            layer.commitChanges()
            return
        # Start: make the layer active, begin editing, and switch to the QGIS
        # Vertex Tool so the user can drag, add and delete vertices. The
        # existing geometryChanged/editingStopped hooks keep FlyPath in sync.
        self.iface.setActiveLayer(layer)
        layer.startEditing()
        self._prev_tool_before_edit = self.iface.mapCanvas().mapTool()
        try:
            self.iface.actionVertexTool().trigger()
        except AttributeError:
            try:
                self.iface.actionNodeTool().trigger()   # older QGIS name
            except AttributeError:
                pass
        self.editPolygonBtn.setText('✓ Finish Editing')
        noun = ('corridor centre line' if self._mission_kind() == 'corridor'
                else 'survey polygon')
        self._set_info(
            f'Editing the {noun}: drag a vertex to move it, click an '
            'edge to add one, or select a vertex and press Delete to remove '
            'it. Click Finish Editing when done.'
        )

    def _finish_polygon_edit(self):
        """Reset the Edit button, restore the previous map tool and info bar."""
        self.editPolygonBtn.setText('✎ Edit')
        tool = getattr(self, '_prev_tool_before_edit', None)
        if tool is not None:
            self.iface.mapCanvas().setMapTool(tool)
            self._prev_tool_before_edit = None
        self._set_info(_INFO_IDLE)

    def _on_drawing_cancelled(self):
        self.drawPolygonBtn.setChecked(False)
        self.drawPolygonBtn.setText(self._draw_btn_default_text())
        self._leave_draw_tool()

    def _cancel_draw_tool(self):
        """Stop drawing, restore the previous map tool, and reset the button."""
        self._leave_draw_tool()
        self.drawPolygonBtn.setChecked(False)
        self.drawPolygonBtn.setText(self._draw_btn_default_text())
