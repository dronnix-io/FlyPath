"""Orbit (3D model) missions: draw a circle, fly one ring facing its centre.

A mixin for FlyPathDialog, in the same style as SurveyLifecycleMixin. It owns
the orbit controls, the circle draw and edit tool, the circle layers and the
orbit planning call. The dialog routes here wherever the mission kind is
'orbit'; planning itself runs in the shared engine (plan_orbit).
"""

import math

from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDoubleSpinBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSpinBox, QWidget,
)
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea,
    QgsFeature, QgsFillSymbol, QgsGeometry, QgsLineSymbol, QgsMarkerSymbol,
    QgsPalLayerSettings, QgsPointXY, QgsProject, QgsTextBufferSettings, QgsTextFormat,
    QgsVectorLayer, QgsVectorLayerSimpleLabeling,
)
from qgis.gui import QgsMapTool, QgsRubberBand

try:
    from . import planning_adapter, preview_layers
    from .hardware import registry
except ImportError:  # standalone tests import modules as top-level packages
    import planning_adapter
    import preview_layers
    from hardware import registry


ORBIT_LABEL = 'Orbit (3D Model)'
DEFAULT_ORBIT_OVERLAP = 90      # % between neighbouring photos around the ring
ORBIT_MIN_ALTITUDE_M = 10.0
MAPPING_MIN_ALTITUDE_M = 30.0
NO_CENTRE_TEXT = '— no centre point —'
DRAW_TEXT = 'Draw Circle on Map'
EDIT_TEXT = '✎ Edit'
_INFO_IDLE = 'ⓘ  Hover over any field to see what it does.'
try:
    _MB_YES = QMessageBox.StandardButton.Yes
    _MB_NO = QMessageBox.StandardButton.No
except AttributeError:
    _MB_YES = getattr(QMessageBox, 'Yes')
    _MB_NO = getattr(QMessageBox, 'No')
try:
    _LINE_PLACEMENT = Qgis.LabelPlacement.Line
except AttributeError:
    _LINE_PLACEMENT = getattr(QgsPalLayerSettings, 'Line')
_WGS84 = QgsCoordinateReferenceSystem('EPSG:4326')
try:
    _ICON_BOX = QgsRubberBand.IconType.ICON_BOX
except AttributeError:
    _ICON_BOX = getattr(QgsRubberBand, 'ICON_BOX')
_HANDLE_INDEXES = (0, 30, 60, 90)   # north, east, south, west on the 3° ring


class OrbitMapTool(QgsMapTool):
    """Draw or edit the orbit circle on the map.

    Drawing: click the centre (the radius stays), or press and drag to set the
    radius as well. Editing: drag inside the circle to move it, or drag a
    handle or the outline to resize it; the pointer shows which one applies,
    and the tool stays on for more edits. on_drag(radius)
    follows a resize drag, on_done(centre, radius) reports each result, and
    on_done(None, None) means right click or Esc (cancel or finish).
    """

    EDGE_TOLERANCE_PX = 10
    DRAG_THRESHOLD_PX = 4

    def __init__(self, canvas, circle, radius_range, ring_points, on_drag, on_done,
                 editing=False):
        super().__init__(canvas)
        self.editing = editing
        self._circle = circle           # returns ((lon, lat) or None, radius)
        self._radius_range = radius_range
        self._ring_points = ring_points
        self._on_drag = on_drag
        self._on_done = on_done
        self._press = None
        self._press_point = None
        self._mode = None               # 'draw', 'resize' or 'move'
        self._centre = None
        self._radius = None
        self._drag_centre = None
        self._area = QgsDistanceArea()
        self._area.setSourceCrs(_WGS84, QgsProject.instance().transformContext())
        self._area.setEllipsoid('WGS84')
        self._ring_band = QgsRubberBand(canvas, Qgis.GeometryType.Polygon)
        self._ring_band.setFillColor(QColor(255, 20, 147, 46))
        self._ring_band.setStrokeColor(QColor('#FF1493'))
        self._ring_band.setWidth(2)
        self._ring_band.setLineStyle(Qt.PenStyle.DashLine)
        self._radius_band = QgsRubberBand(canvas, Qgis.GeometryType.Line)
        self._radius_band.setColor(QColor('#FF1493'))
        self._radius_band.setWidth(2)
        self._handle_band = None
        if editing:
            # Square resize handles on the circle, like the QGIS vertex tool.
            self._handle_band = QgsRubberBand(canvas, Qgis.GeometryType.Point)
            self._handle_band.setIcon(_ICON_BOX)
            self._handle_band.setIconSize(10)
            self._handle_band.setColor(QColor('#FF1493'))
            self._handle_band.setFillColor(QColor('white'))
            self._handle_band.setStrokeColor(QColor('#FF1493'))
            self._handle_band.setWidth(2)
            self.refresh_handles()
        self.setCursor(Qt.CursorShape.CrossCursor)

    def refresh_handles(self):
        """Put the resize handles on the current circle."""
        centre, radius = self._circle()
        if centre is None:
            if self._handle_band is not None:
                self._handle_band.reset(Qgis.GeometryType.Point)
            return
        self._show_handles(QgsPointXY(*centre), radius)

    def _show_handles(self, centre, radius):
        if self._handle_band is None:
            return
        ring = self._ring_points((centre.x(), centre.y()), radius)
        self._handle_band.setToGeometry(
            QgsGeometry.fromMultiPointXY([ring[index] for index in _HANDLE_INDEXES]), _WGS84)

    def _edit_mode(self, pos, point, centre, radius):
        """'resize' on the outline or a handle, 'move' inside, else None."""
        tolerance = self._metres(point, self._to_wgs84(pos + QPoint(self.EDGE_TOLERANCE_PX, 0)))
        distance = self._metres(centre, point)
        if abs(distance - radius) <= tolerance:
            return 'resize'
        return 'move' if distance < radius else None

    def _to_wgs84(self, pos):
        transform = QgsCoordinateTransform(
            self.canvas().mapSettings().destinationCrs(), _WGS84, QgsProject.instance())
        return transform.transform(self.toMapCoordinates(pos))

    def _metres(self, first, second):
        return self._area.measureLine(QgsPointXY(first), QgsPointXY(second))

    def _dragged(self, pos):
        return (pos - self._press).manhattanLength() > self.DRAG_THRESHOLD_PX

    def _radius_to(self, point):
        low, high = self._radius_range
        return min(high, max(low, self._metres(self._drag_centre, point)))

    def _moved_centre(self, point):
        return QgsPointXY(self._centre.x() + point.x() - self._press_point.x(),
                          self._centre.y() + point.y() - self._press_point.y())

    def _result(self, pos):
        """The (centre, radius) a drag to pos gives, for the current mode."""
        point = self._to_wgs84(pos)
        if self._mode == 'move':
            return self._moved_centre(point), self._radius, None
        return self._drag_centre, self._radius_to(point), point

    def canvasPressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._on_done(None, None)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.pos()
        point = self._to_wgs84(pos)
        centre, self._radius = self._circle()
        self._centre = QgsPointXY(*centre) if centre is not None else None
        self._mode = 'draw'
        if self.editing:
            if self._centre is None:
                return
            self._mode = self._edit_mode(pos, point, self._centre, self._radius)
            if self._mode is None:
                return                  # outside the circle: nothing to edit
        self._press = pos
        self._press_point = point
        self._drag_centre = point if self._mode == 'draw' else QgsPointXY(self._centre)

    def canvasMoveEvent(self, event):
        if self._press is None:
            if self.editing:
                self._hover(event.pos())
            return
        if not self._dragged(event.pos()):
            return
        centre, radius, edge = self._result(event.pos())
        self._show_handles(centre, radius)
        ring = self._ring_points((centre.x(), centre.y()), radius)
        self._ring_band.setToGeometry(QgsGeometry.fromPolygonXY([ring + [ring[0]]]), _WGS84)
        self._radius_band.setToGeometry(
            QgsGeometry.fromPolylineXY([centre, edge or ring[0]]), _WGS84)
        if self._mode != 'move':
            self._on_drag(radius)

    def _hover(self, pos):
        """Show what a drag from here would do: resize, move or nothing."""
        centre, radius = self._circle()
        mode = (self._edit_mode(pos, self._to_wgs84(pos), QgsPointXY(*centre), radius)
                if centre is not None else None)
        self.setCursor({'resize': Qt.CursorShape.SizeFDiagCursor,
                        'move': Qt.CursorShape.SizeAllCursor}.get(mode, Qt.CursorShape.CrossCursor))

    def canvasReleaseEvent(self, event):
        if self._press is None or event.button() != Qt.MouseButton.LeftButton:
            return
        dragged = self._dragged(event.pos())
        centre, radius = self._result(event.pos())[:2]
        mode = self._mode
        self._press = None
        self._clear_bands()
        if dragged:
            self._on_done((centre.x(), centre.y()), radius)
        elif mode == 'draw':
            self._on_done((centre.x(), centre.y()), None)   # a click keeps the radius

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_done(None, None)

    def _clear_bands(self):
        if self._ring_band is not None:
            self._ring_band.reset(Qgis.GeometryType.Polygon)
            self._radius_band.reset(Qgis.GeometryType.Line)

    def deactivate(self):
        self._press = None
        for band in (self._ring_band, self._radius_band, self._handle_band):
            if band is not None:
                self.canvas().scene().removeItem(band)
        self._ring_band = self._radius_band = self._handle_band = None
        super().deactivate()


class OrbitMixin:
    """Orbit mission controls and planning for the FlyPath panel."""

    def _orbit_init_state(self):
        self._orbit_centre = None           # (lon, lat) in WGS84
        self._orbit_centre_layer_id = None
        self._orbit_ring_layer_id = None    # the circle the drone flies, shown at once
        self._orbit_radius_layer_id = None  # centre to start point, labelled with the radius
        self._orbit_tool = None
        self._orbit_prev_tool = None
        self._orbit_saved_overlap = None    # mapping side overlap, restored on leaving
        self._orbit_headings = None
        self._mapping_reverse_tip = None    # Reverse route tooltip outside orbits

    # ── Building the controls ─────────────────────────────────────────────

    def _build_orbit_area_row(self, form):
        """Centre and Draw rows for the Survey Area group (replace Source)."""
        self.orbitCentreLabel = QLabel(NO_CENTRE_TEXT)
        self.orbitCentreLabel.setObjectName('selectionInfo')
        self._tip(self.orbitCentreLabel,
            'Latitude and longitude of the centre point the drone orbits.')
        form.addRow('Centre Point', self.orbitCentreLabel)
        self._set_row_visible(form, self.orbitCentreLabel, False)

        # Draw / edit / remove, like the Draw source of 2D mapping.
        self.drawCircleBtn = QPushButton(DRAW_TEXT)
        self.drawCircleBtn.setObjectName('drawCircleBtn')
        self.drawCircleBtn.setCheckable(True)
        self._tip(self.drawCircleBtn,
            'Click the object to orbit, or press on it and drag out to set the '
            'radius too. The drone flies this circle, always facing the centre. '
            'Right click or Escape cancels.')
        self.editCircleBtn = QPushButton(EDIT_TEXT)
        self.editCircleBtn.setObjectName('editCircleBtn')
        self.editCircleBtn.setCheckable(True)
        self._tip(self.editCircleBtn,
            'Edit the circle: drag inside it to move it, or drag a square '
            'handle or the outline to make it larger or smaller. Click Finish '
            'Editing when done.')
        self.editCircleBtn.setVisible(False)
        self.removeCircleBtn = QPushButton('✕ Remove')
        self.removeCircleBtn.setObjectName('removeCircleBtn')
        self._tip(self.removeCircleBtn, 'Remove the circle and its waypoints.')
        self.removeCircleBtn.setVisible(False)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 6, 0, 0)   # a little more room under Centre Point
        layout.setSpacing(4)
        layout.addWidget(self.drawCircleBtn)
        layout.addWidget(self.editCircleBtn)
        layout.addWidget(self.removeCircleBtn)
        for button in (self.drawCircleBtn, self.editCircleBtn, self.removeCircleBtn):
            button.setMinimumHeight(28)     # same height as the 2D Draw buttons
        self._orbitDrawRow = row
        form.addRow(row)
        self._set_row_visible(form, row, False)

    def _build_orbit_flight_rows(self, form):
        """Radius, camera tilt and direction rows for the Flight Parameters."""
        self.orbitRadiusSpin = QDoubleSpinBox()
        self.orbitRadiusSpin.setRange(5.0, 2000.0)
        self.orbitRadiusSpin.setValue(30.0)
        self.orbitRadiusSpin.setSingleStep(5.0)
        self.orbitRadiusSpin.setDecimals(1)
        self.orbitRadiusSpin.setSuffix(' m')
        self._tip(self.orbitRadiusSpin,
            'Distance from the centre to the flight circle. Leave enough room '
            'around the object for a safe flight.')
        form.addRow('Radius', self.orbitRadiusSpin)

        self.orbitTiltSpin = QSpinBox()
        self.orbitTiltSpin.setRange(-90, 0)
        self.orbitTiltSpin.setValue(-35)
        self.orbitTiltSpin.setSingleStep(5)
        self.orbitTiltSpin.setSuffix(' °')
        self._tip(self.orbitTiltSpin,
            'Camera tilt. 0° looks at the horizon, -90° straight down. Oblique '
            'angles between -30° and -45° suit 3D models.')
        form.addRow('Camera Tilt', self.orbitTiltSpin)

        for widget in (self.orbitRadiusSpin, self.orbitTiltSpin):
            self._set_row_visible(form, widget, False)

    def _orbit_connect_signals(self):
        self.drawCircleBtn.toggled.connect(self._on_draw_circle_toggled)
        self.editCircleBtn.toggled.connect(self._on_edit_circle_toggled)
        self.removeCircleBtn.clicked.connect(self._on_remove_orbit_circle)
        self.orbitRadiusSpin.valueChanged.connect(self._show_orbit_centre)
        self.orbitRadiusSpin.valueChanged.connect(self._on_param_changed)
        self.orbitTiltSpin.valueChanged.connect(self._on_param_changed)

    # ── Layout ────────────────────────────────────────────────────────────

    def _apply_orbit_layout(self, orbit):
        """Show the orbit controls and hide the mapping ones, or the reverse.

        Only visibility, labels and limits change here, so it is safe to rerun
        at any time, for example when a failed import restores the panel.
        _switch_orbit_mode() changes the values when the mission kind changes."""
        for widget in (self.orbitRadiusSpin, self.orbitTiltSpin):
            self._set_row_visible(self._flight_form, widget, orbit)
        self._set_row_visible(self._flight_form, self.gsdSpin, not orbit)
        self._set_row_visible(self._area_form, self.orbitCentreLabel, orbit)
        self._set_row_visible(self._area_form, self._orbitDrawRow, orbit)
        self._set_row_visible(self._area_form, self._sourceRow, not orbit)
        self._set_row_visible(self._area_form, self._sourceStack, not orbit)

        # An orbit always flies a curved path, so the path choice is hidden.
        for widget in (self._pathInlineLabel, self.pathCurvedRadio, self.pathStraightRadio):
            widget.setVisible(not orbit)

        # One ring: no splitting or terrain follow.
        drone = self.droneModelCombo.currentText()
        splitting = (not orbit and registry.has(drone)
                     and registry.get(drone).category == 'consumer')
        self._set_row_visible(self._organizer_form, self.splitCheck, splitting)
        self._set_row_visible(self._organizer_form, self.splitSpin, splitting)
        self.terrainFollowCheck.setVisible(not orbit)
        # Reverse route sets the orbit direction: clockwise, or reversed to
        # counterclockwise. It keeps its usual place in the route options row.
        hint = next((child for child in self.reverseRouteCheck.children()
                     if hasattr(child, '_text')), None)     # the panel's hover hint
        if hint is not None and self._mapping_reverse_tip is None:
            self._mapping_reverse_tip = hint._text
        if orbit:
            self.reverseRouteCheck.setVisible(True)
            self.reverseRouteCheck.setEnabled(True)
        if hint is not None:
            hint._text = ((
                'Fly the ring counterclockwise (seen from above) instead of '
                'clockwise. The drone starts at the north point of the circle '
                'either way.') if orbit else self._mapping_reverse_tip)
        # The takeoff zone and contours are built for mapping grids.
        self._takeoffGroup.setVisible(not orbit)
        # Orbits may fly lower than mapping.
        blocked = self.altitudeSpin.blockSignals(True)
        self.altitudeSpin.setMinimum(ORBIT_MIN_ALTITUDE_M if orbit else MAPPING_MIN_ALTITUDE_M)
        self.altitudeSpin.blockSignals(blocked)

    def _switch_orbit_mode(self, orbit):
        """Change values when the mission kind switches into or out of orbit."""
        if orbit:
            self.pathCurvedRadio.setChecked(True)   # DJI Fly keeps curved paths on re-save
            self.splitCheck.setChecked(False)
            self.terrainFollowCheck.setChecked(False)
            for button in (self.showTakeoffZoneBtn, self.showContoursBtn):
                if button.isCheckable() and button.isChecked():
                    button.setChecked(False)
        # Reverse means a different thing in each kind, so a switch clears it.
        self.reverseRouteCheck.setChecked(False)
        # Orbits need a denser ring of photos than mapping lines.
        blocked = self.sideOverlapSpin.blockSignals(True)
        if orbit and self._orbit_saved_overlap is None:
            self._orbit_saved_overlap = self.sideOverlapSpin.value()
            self.sideOverlapSpin.setValue(DEFAULT_ORBIT_OVERLAP)
        elif not orbit and self._orbit_saved_overlap is not None:
            self.sideOverlapSpin.setValue(self._orbit_saved_overlap)
            self._orbit_saved_overlap = None
        self.sideOverlapSpin.blockSignals(blocked)
        if not orbit:
            self._clear_orbit_centre()

    def _apply_orbit_capabilities(self, full):
        """Full auto sets the photo overlap; semi auto shows the resulting one."""
        self._set_row_visible(self._flight_form, self.sideOverlapSpin, full)
        self._set_row_visible(self._flight_form, self.frontOverlapStack, not full)
        self.frontOverlapStack.setCurrentIndex(0)
        label = self._row_label(self._flight_form, self.frontOverlapStack)
        if label is not None:
            label.setText('Side Overlap')
        self._set_row_visible(self._area_form, self.demCombo, False)

    def _restore_mapping_overlap_rows(self):
        self._set_row_visible(self._flight_form, self.sideOverlapSpin, True)
        self._set_row_visible(self._flight_form, self.frontOverlapStack, True)
        label = self._row_label(self._flight_form, self.frontOverlapStack)
        if label is not None:
            label.setText('Front Overlap')

    # ── Circle draw / edit tool ───────────────────────────────────────────

    def _orbit_circle(self):
        return self._orbit_centre, self.orbitRadiusSpin.value()

    def _start_orbit_tool(self, editing):
        """Switch the map to the circle tool, in draw or edit mode."""
        canvas = self.iface.mapCanvas()
        if self._orbit_tool is None:
            self._orbit_prev_tool = canvas.mapTool()
        other = self.drawCircleBtn if editing else self.editCircleBtn
        self._reset_orbit_button(other)
        self._orbit_tool = OrbitMapTool(
            canvas, self._orbit_circle,
            (self.orbitRadiusSpin.minimum(), self.orbitRadiusSpin.maximum()),
            self._orbit_ring_points, self._on_orbit_drag, self._on_orbit_drawn,
            editing=editing)
        canvas.setMapTool(self._orbit_tool)

    def _reset_orbit_button(self, button):
        blocked = button.blockSignals(True)
        button.setChecked(False)
        button.setText(DRAW_TEXT if button is self.drawCircleBtn else EDIT_TEXT)
        button.blockSignals(blocked)

    def _on_draw_circle_toggled(self, checked):
        if not checked:
            self._leave_orbit_tool()
            return
        if self._orbit_centre is not None:
            reply = QMessageBox.question(
                self, 'Replace Orbit Circle?',
                'An orbit circle is already defined.\n\n'
                'Do you want to discard it and draw a new one?',
                _MB_YES | _MB_NO, _MB_NO)
            if reply != _MB_YES:
                self._reset_orbit_button(self.drawCircleBtn)
                return
            self._on_remove_orbit_circle()
            blocked = self.drawCircleBtn.blockSignals(True)
            self.drawCircleBtn.setChecked(True)
            self.drawCircleBtn.blockSignals(blocked)
        self._start_orbit_tool(editing=False)
        self.drawCircleBtn.setText('Click the centre, drag for the radius…')

    def _on_edit_circle_toggled(self, checked):
        if not checked:
            self._leave_orbit_tool()
            return
        if self._orbit_centre is None:
            self._reset_orbit_button(self.editCircleBtn)
            return
        self._start_orbit_tool(editing=True)
        self.editCircleBtn.setText('✓ Finish Editing')
        self._set_info(
            'Editing the orbit circle: drag inside it to move it, or drag a '
            'square handle or the outline to make it larger or smaller. Click '
            'Finish Editing when done.')

    def _leave_orbit_tool(self):
        """Turn the circle tool off and restore the earlier map tool."""
        canvas = self.iface.mapCanvas()
        tool, self._orbit_tool = self._orbit_tool, None
        if tool is not None:
            if self._orbit_prev_tool is not None:
                canvas.setMapTool(self._orbit_prev_tool)
            elif canvas.mapTool() is tool:
                canvas.unsetMapTool(tool)
            if tool.editing:
                self._set_info(_INFO_IDLE)
        self._orbit_prev_tool = None
        self._reset_orbit_button(self.drawCircleBtn)
        self._reset_orbit_button(self.editCircleBtn)

    def _on_orbit_drag(self, radius):
        button = self.editCircleBtn if self.editCircleBtn.isChecked() else self.drawCircleBtn
        button.setText(f'Radius {radius:.1f} m')

    def _on_orbit_drawn(self, centre, radius):
        """Apply a circle from the map tool; drawing ends, editing goes on."""
        editing = self._orbit_tool is not None and self._orbit_tool.editing
        if centre is None or not editing:
            self._leave_orbit_tool()
        else:
            self.editCircleBtn.setText('✓ Finish Editing')
        if centre is None:
            return
        if radius is not None:
            blocked = self.orbitRadiusSpin.blockSignals(True)
            self.orbitRadiusSpin.setValue(radius)
            self.orbitRadiusSpin.blockSignals(blocked)
        self.set_orbit_centre(*centre)
        self._on_param_changed()
        # Show the waypoints right away; once shown, they follow later edits.
        if not self._preview_layer_ids:
            self._on_preview()

    def _on_remove_orbit_circle(self):
        """Remove the circle and its waypoints, like Remove in 2D Draw."""
        self._on_clear_preview(reset_area=True)

    def set_orbit_centre(self, longitude, latitude):
        """Set the orbit centre (WGS84) and draw its marker."""
        self._orbit_centre = (float(longitude), float(latitude))
        self.orbitCentreLabel.setText(f'{latitude:.6f}, {longitude:.6f}')
        self._show_orbit_centre()

    def _orbit_ring_points(self, centre=None, radius=None):
        """Points on the flight circle every 3°, starting north like the engine."""
        area = QgsDistanceArea()
        area.setSourceCrs(_WGS84, QgsProject.instance().transformContext())
        area.setEllipsoid('WGS84')
        centre = QgsPointXY(*(centre or self._orbit_centre))
        radius = self.orbitRadiusSpin.value() if radius is None else radius
        return [area.computeSpheroidProject(centre, radius, math.radians(bearing))
                for bearing in range(0, 360, 3)]

    def _add_orbit_radius_layer(self, start):
        """A line from the centre to the start point, labelled with the radius."""
        layer = QgsVectorLayer('LineString?crs=EPSG:4326&field=label:string',
                               'FlyPath — Orbit Radius', 'memory')
        layer.setCustomProperty('flypath_internal', True)
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(*self._orbit_centre), start]))
        feature.setAttribute('label', f'R {self.orbitRadiusSpin.value():g} m')
        layer.dataProvider().addFeatures([feature])
        layer.renderer().setSymbol(QgsLineSymbol.createSimple({
            'line_color': '#FF1493', 'line_width': '0.6', 'line_style': 'dash',
        }))
        text = QgsTextFormat()
        text.setColor(QColor('#FF1493'))
        text.setSize(10)
        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setColor(QColor('white'))
        buffer.setSize(1)
        text.setBuffer(buffer)
        settings = QgsPalLayerSettings()
        settings.fieldName = 'label'
        settings.placement = _LINE_PLACEMENT
        settings.setFormat(text)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        preview_layers.register(layer, kind='orbit_radius')
        self._orbit_radius_layer_id = layer.id()

    def _show_orbit_centre(self):
        """Draw the orbit circle, radius and centre as soon as the centre is set."""
        self._remove_orbit_centre_layer()
        if self._orbit_centre is None:
            return
        points = self._orbit_ring_points()
        ring = QgsVectorLayer('Polygon?crs=EPSG:4326', 'FlyPath — Orbit Circle', 'memory')
        ring.setCustomProperty('flypath_internal', True)
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPolygonXY([points + [points[0]]]))
        ring.dataProvider().addFeatures([feature])
        ring.renderer().setSymbol(QgsFillSymbol.createSimple({
            'color': '255,20,147,46', 'outline_color': '#FF1493',
            'outline_width': '0.8', 'outline_style': 'dash',
        }))
        preview_layers.register(ring, kind='orbit_ring')
        self._orbit_ring_layer_id = ring.id()
        self._add_orbit_radius_layer(points[0])

        layer =QgsVectorLayer('Point?crs=EPSG:4326', 'FlyPath — Orbit Centre', 'memory')
        layer.setCustomProperty('flypath_internal', True)
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*self._orbit_centre)))
        layer.dataProvider().addFeatures([feature])
        layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({
            'name': 'cross2', 'color': '#FF8A78', 'outline_color': '#FF8A78',
            'outline_width': '0.8', 'size': '6',
        }))
        preview_layers.register(layer, kind='orbit_centre')
        self._orbit_centre_layer_id = layer.id()
        if self._orbit_tool is not None and self._orbit_tool.editing:
            self._orbit_tool.refresh_handles()
        self.editCircleBtn.setVisible(True)
        self.removeCircleBtn.setVisible(True)
        self.iface.mapCanvas().refresh()

    def _remove_orbit_centre_layer(self):
        """Remove the orbit circle, radius and centre marker layers."""
        layer_ids = [layer_id for layer_id in (
            self._orbit_centre_layer_id, self._orbit_ring_layer_id,
            self._orbit_radius_layer_id) if layer_id]
        if layer_ids:
            preview_layers.remove(layer_ids)
        self._orbit_centre_layer_id = None
        self._orbit_ring_layer_id = None
        self._orbit_radius_layer_id = None

    def _clear_orbit_centre(self):
        self._leave_orbit_tool()
        self._orbit_centre = None
        self._orbit_headings = None
        self.orbitCentreLabel.setText(NO_CENTRE_TEXT)
        self._remove_orbit_centre_layer()
        self.editCircleBtn.setVisible(False)
        self.removeCircleBtn.setVisible(False)

    def _has_orbit_centre(self, silent=False):
        if self._orbit_centre is None:
            if not silent:
                QMessageBox.information(
                    self, 'No Orbit Centre',
                    'Click Draw Circle on Map, then click the object to orbit.')
            return False
        return True

    # ── Planning ──────────────────────────────────────────────────────────

    def _orbit_request_from_ui(self):
        drone = registry.get(self.droneModelCombo.currentText())
        if drone.category != 'consumer' or not drone.website_code:
            raise ValueError('Orbit missions need a DJI Fly drone; %s is not supported.'
                             % drone.name)
        return planning_adapter.build_orbit_request(
            centre=self._orbit_centre,
            drone_profile_id=drone.website_code,
            radius_m=self.orbitRadiusSpin.value(),
            altitude_m=self.altitudeSpin.value(),
            gimbal_pitch_deg=self.orbitTiltSpin.value(),
            clockwise=not self.reverseRouteCheck.isChecked(),
            speed_m_s=self.speedSpin.value(),
            capture_mode=self._mission_type(),
            side_overlap_ratio=self.sideOverlapSpin.value() / 100.0,
            finish_action=self.finishActionCombo.currentText(),
            max_waypoints_per_flight=self.maxWaypointsSpin.value(),
        )

    def _plan_orbit(self, silent=True):
        """Plan the orbit in the engine and show it in the panel, or None."""
        try:
            request = self._orbit_request_from_ui()
            result = planning_adapter.plan(request)
            flights = self._apply_planning_result(request, result)
            self._orbit_headings = flights[0]['headings']
            self.linesLabel.setText('1 ring')
            if self._mission_type() != 'full':
                self.frontOverlapLabel.setText(
                    f"{result['capture']['side_overlap_ratio'] * 100:.0f} %")
            if not result['validation']['export_allowed']:
                self._set_info(result['validation']['errors'][0]['message'])
            return result
        except (ValueError, planning_adapter.PlanningError) as exc:
            self._planning.plan_failed()
            self._waypoints = []
            self._missions = []
            self._orbit_headings = None
            self._shot_spacing_m = 0.0
            if not silent:
                QMessageBox.warning(self, 'Cannot Plan Orbit', str(exc))
            return None
