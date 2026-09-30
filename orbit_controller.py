"""Orbit (3D model) missions: pick a centre, fly one ring facing it.

A mixin for FlyPathDialog, in the same style as SurveyLifecycleMixin. It owns
the orbit controls, the centre pick tool, the centre marker layer and the
orbit planning call. The dialog routes here wherever the mission kind is
'orbit'; planning itself runs in the shared engine (plan_orbit).
"""

import math

from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
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
NO_CENTRE_TEXT = '— no centre —'
try:
    _LINE_PLACEMENT = Qgis.LabelPlacement.Line
except AttributeError:
    _LINE_PLACEMENT = getattr(QgsPalLayerSettings, 'Line')
_WGS84 = QgsCoordinateReferenceSystem('EPSG:4326')


class OrbitMapTool(QgsMapTool):
    """Click the orbit centre, or press and drag to draw the circle.

    Pressing on the edge of the current circle and dragging resizes it and
    keeps the centre. on_drag(radius) follows the drag; on_done(centre,
    radius) reports the result, with radius None for a plain click and both
    None when cancelled (right click or Esc).
    """

    EDGE_TOLERANCE_PX = 10
    DRAG_THRESHOLD_PX = 4

    def __init__(self, canvas, centre, radius, radius_range, ring_points, on_drag, on_done):
        super().__init__(canvas)
        self._centre = QgsPointXY(*centre) if centre is not None else None
        self._radius = radius
        self._radius_range = radius_range
        self._ring_points = ring_points
        self._on_drag = on_drag
        self._on_done = on_done
        self._press = None
        self._drag_centre = None
        self._resizing = False
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
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _to_wgs84(self, point):
        transform = QgsCoordinateTransform(
            self.canvas().mapSettings().destinationCrs(), _WGS84, QgsProject.instance())
        return transform.transform(point)

    def _metres(self, first, second):
        return self._area.measureLine(QgsPointXY(first), QgsPointXY(second))

    def _dragged(self, pos):
        return (pos - self._press).manhattanLength() > self.DRAG_THRESHOLD_PX

    def _radius_to(self, pos):
        point = self._to_wgs84(self.toMapCoordinates(pos))
        low, high = self._radius_range
        return min(high, max(low, self._metres(self._drag_centre, point))), point

    def canvasPressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._on_done(None, None)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.pos()
        point = self._to_wgs84(self.toMapCoordinates(pos))
        self._press = pos
        self._resizing = False
        if self._centre is not None:
            near = self._to_wgs84(self.toMapCoordinates(
                pos + QPoint(self.EDGE_TOLERANCE_PX, 0)))
            tolerance = self._metres(point, near)
            self._resizing = abs(self._metres(self._centre, point) - self._radius) <= tolerance
        self._drag_centre = QgsPointXY(self._centre) if self._resizing else point

    def canvasMoveEvent(self, event):
        if self._press is None or not self._dragged(event.pos()):
            return
        radius, point = self._radius_to(event.pos())
        ring = self._ring_points((self._drag_centre.x(), self._drag_centre.y()), radius)
        self._ring_band.setToGeometry(QgsGeometry.fromPolygonXY([ring + [ring[0]]]), _WGS84)
        self._radius_band.setToGeometry(
            QgsGeometry.fromPolylineXY([self._drag_centre, point]), _WGS84)
        self._on_drag(radius)

    def canvasReleaseEvent(self, event):
        if self._press is None or event.button() != Qt.MouseButton.LeftButton:
            return
        dragged = self._dragged(event.pos())
        radius = self._radius_to(event.pos())[0] if dragged else None
        centre = (self._drag_centre.x(), self._drag_centre.y())
        self._press = None
        self._clear_bands()
        if self._resizing and not dragged:
            self._on_done(centre, self._radius)     # a click on the edge changes nothing
        else:
            self._on_done(centre, radius)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_done(None, None)

    def _clear_bands(self):
        if self._ring_band is not None:
            self._ring_band.reset(Qgis.GeometryType.Polygon)
            self._radius_band.reset(Qgis.GeometryType.Line)

    def deactivate(self):
        self._press = None
        for band in (self._ring_band, self._radius_band):
            if band is not None:
                self.canvas().scene().removeItem(band)
        self._ring_band = self._radius_band = None
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

    # ── Building the controls ─────────────────────────────────────────────

    def _build_orbit_area_row(self, form):
        """Centre row for the Survey Area group (replaces Source for orbits)."""
        self.orbitCentreLabel = QLabel(NO_CENTRE_TEXT)
        self.orbitCentreLabel.setObjectName('selectionInfo')
        self._tip(self.orbitCentreLabel, 'Latitude and longitude of the orbit centre.')
        self.pickCentreBtn = QPushButton('Pick Centre on Map')
        self.pickCentreBtn.setObjectName('pickCentreBtn')
        self.pickCentreBtn.setCheckable(True)
        self.pickCentreBtn.setMinimumHeight(28)
        self._tip(self.pickCentreBtn,
            'Click the object to orbit on the map, or press and drag to draw the '
            'circle. Press on the edge of the circle and drag to resize it. The '
            'drone flies this circle, always facing the centre. Right click or '
            'Esc cancels.')
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.orbitCentreLabel, 1)
        layout.addWidget(self.pickCentreBtn)
        self._orbitCentreRow = row
        form.addRow('Centre', row)
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

        self.orbitDirectionCombo = QComboBox()
        self.orbitDirectionCombo.addItem('Clockwise')
        self.orbitDirectionCombo.addItem('Counterclockwise')
        self._tip(self.orbitDirectionCombo,
            'Which way the drone flies around the centre, seen from above.')
        form.addRow('Orbit Direction', self.orbitDirectionCombo)

        for widget in (self.orbitRadiusSpin, self.orbitTiltSpin, self.orbitDirectionCombo):
            self._set_row_visible(form, widget, False)

    def _orbit_connect_signals(self):
        self.pickCentreBtn.toggled.connect(self._on_pick_centre_toggled)
        self.orbitRadiusSpin.valueChanged.connect(self._show_orbit_centre)
        self.orbitRadiusSpin.valueChanged.connect(self._on_param_changed)
        self.orbitTiltSpin.valueChanged.connect(self._on_param_changed)
        self.orbitDirectionCombo.currentIndexChanged.connect(self._on_param_changed)

    # ── Layout ────────────────────────────────────────────────────────────

    def _apply_orbit_layout(self, orbit):
        """Show the orbit controls and hide the mapping ones, or the reverse."""
        for widget in (self.orbitRadiusSpin, self.orbitTiltSpin, self.orbitDirectionCombo):
            self._set_row_visible(self._flight_form, widget, orbit)
        self._set_row_visible(self._flight_form, self.gsdSpin, not orbit)
        self._set_row_visible(self._area_form, self._orbitCentreRow, orbit)
        self._set_row_visible(self._area_form, self._sourceRow, not orbit)
        self._sourceStack.setVisible(not orbit)

        # An orbit always flies a curved path: DJI Fly keeps it on re-save.
        if orbit:
            self.pathCurvedRadio.setChecked(True)
        for widget in (self._pathInlineLabel, self.pathCurvedRadio, self.pathStraightRadio):
            widget.setVisible(not orbit)

        # One ring, no splitting, breaks, cross-hatch, reverse or terrain follow.
        if orbit:
            self.splitCheck.setChecked(False)
            self.terrainFollowCheck.setChecked(False)
        self._set_row_visible(self._organizer_form, self.splitCheck, not orbit)
        self._set_row_visible(self._organizer_form, self.splitSpin, not orbit)
        self.terrainFollowCheck.setVisible(not orbit)
        # The takeoff zone and contours are built for mapping grids.
        if orbit:
            for button in (self.showTakeoffZoneBtn, self.showContoursBtn):
                if button.isCheckable() and button.isChecked():
                    button.setChecked(False)
        self._takeoffGroup.setVisible(not orbit)

        # Orbits fly lower than mapping and need a denser ring of photos.
        blocked = self.sideOverlapSpin.blockSignals(True)
        if orbit:
            self._orbit_saved_overlap = self.sideOverlapSpin.value()
            self.sideOverlapSpin.setValue(DEFAULT_ORBIT_OVERLAP)
        elif self._orbit_saved_overlap is not None:
            self.sideOverlapSpin.setValue(self._orbit_saved_overlap)
            self._orbit_saved_overlap = None
        self.sideOverlapSpin.blockSignals(blocked)
        blocked = self.altitudeSpin.blockSignals(True)
        self.altitudeSpin.setMinimum(ORBIT_MIN_ALTITUDE_M if orbit else MAPPING_MIN_ALTITUDE_M)
        self.altitudeSpin.blockSignals(blocked)
        if not orbit:
            self._clear_orbit_centre()

    def _apply_orbit_capabilities(self, full):
        """Full auto sets the photo overlap; semi auto shows the resulting one."""
        self._set_row_visible(self._flight_form, self.sideOverlapSpin, full)
        self._set_row_visible(self._flight_form, self.frontOverlapStack, not full)
        self.frontOverlapStack.setCurrentIndex(0)
        label = self._flight_form.labelForField(self.frontOverlapStack)
        if label is not None:
            label.setText('Side Overlap')
        self._set_row_visible(self._area_form, self.demCombo, False)

    def _restore_mapping_overlap_rows(self):
        self._set_row_visible(self._flight_form, self.sideOverlapSpin, True)
        self._set_row_visible(self._flight_form, self.frontOverlapStack, True)
        label = self._flight_form.labelForField(self.frontOverlapStack)
        if label is not None:
            label.setText('Front Overlap')

    # ── Centre pick tool and marker ───────────────────────────────────────

    def _on_pick_centre_toggled(self, checked):
        canvas = self.iface.mapCanvas()
        if checked:
            self._orbit_prev_tool = canvas.mapTool()
            self._orbit_tool = OrbitMapTool(
                canvas, self._orbit_centre, self.orbitRadiusSpin.value(),
                (self.orbitRadiusSpin.minimum(), self.orbitRadiusSpin.maximum()),
                self._orbit_ring_points, self._on_orbit_drag, self._on_orbit_drawn)
            canvas.setMapTool(self._orbit_tool)
            self.pickCentreBtn.setText('Click the centre, drag for the radius…')
        else:
            self._leave_orbit_tool()

    def _leave_orbit_tool(self):
        canvas = self.iface.mapCanvas()
        if self._orbit_tool is not None:
            if self._orbit_prev_tool is not None:
                canvas.setMapTool(self._orbit_prev_tool)
            elif canvas.mapTool() is self._orbit_tool:
                canvas.unsetMapTool(self._orbit_tool)
        self._orbit_tool = None
        self._orbit_prev_tool = None
        self.pickCentreBtn.setText('Pick Centre on Map')

    def _on_orbit_drag(self, radius):
        self.pickCentreBtn.setText(f'Radius {radius:.1f} m')

    def _on_orbit_drawn(self, centre, radius):
        """Apply a centre (and a dragged radius) from the map tool."""
        self.pickCentreBtn.setChecked(False)    # turns the map tool off
        if centre is None:
            return
        if radius is not None:
            blocked = self.orbitRadiusSpin.blockSignals(True)
            self.orbitRadiusSpin.setValue(radius)
            self.orbitRadiusSpin.blockSignals(blocked)
        self.set_orbit_centre(*centre)
        self._on_param_changed()

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
        if self.pickCentreBtn.isChecked():
            self.pickCentreBtn.setChecked(False)
        self._orbit_centre = None
        self._orbit_headings = None
        self.orbitCentreLabel.setText(NO_CENTRE_TEXT)
        self._remove_orbit_centre_layer()

    def _has_orbit_centre(self, silent=False):
        if self._orbit_centre is None:
            if not silent:
                QMessageBox.information(
                    self, 'No Orbit Centre',
                    'Click Pick Centre on Map, then click the object to orbit.')
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
            clockwise=self.orbitDirectionCombo.currentIndex() == 0,
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
