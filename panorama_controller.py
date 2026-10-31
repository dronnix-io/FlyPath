"""Panorama missions: photos all around one or more points, for stitched panoramas.

A mixin for FlyPathDialog, in the same style as OrbitMixin. It owns the
panorama controls, the point tool, the point markers and the request
translation. The dialog's shared planning flow runs the request in the engine
(plan_panorama), which flies small outward-facing laps around each point, one
per camera row, because DJI Fly missions cannot turn the drone in place.

Points: each click adds a point, numbered in flight order, and dragging a
point moves it; Undo removes the last one. One point is a single panorama.
Area takes a survey
polygon from the usual Draw, Layer or Selection source, and the engine places
the points on a hexagon grid inside it, by default one altitude apart.
"""

import sys

from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QButtonGroup, QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox,
    QWidget,
)
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsGeometry,
    QgsMarkerSymbol, QgsPalLayerSettings, QgsPointXY, QgsProject, QgsTextBufferSettings,
    QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
)
from qgis.gui import QgsMapTool, QgsRubberBand

from .map_tools import FLYPATH_TOOL_NAME, is_flypath_tool, previous_tool

try:
    from . import form_rows, planning_adapter, preview_layers, survey_geometry
    from .hardware import registry
except ImportError:  # standalone tests import modules as top-level packages
    import form_rows
    import planning_adapter
    import preview_layers
    import survey_geometry
    from hardware import registry


PANORAMA_LABEL = 'Panorama'
DEFAULT_PANORAMA_OVERLAP = 30   # % between neighbouring photos, as DJI's own panoramas
DEFAULT_PANORAMA_MAX_WAYPOINTS = 200   # whole panoramas per flight, as many as fit
PANORAMA_MIN_ALTITUDE_M = 10.0
NO_POINT_TEXT = '— no panorama point —'
PLACE_TEXT = 'Place Points on Map'
PLACING_TEXT = 'Click to add, drag to move…'
_INFO_IDLE = 'ⓘ  Hover over any field to see what it does.'
_WGS84 = QgsCoordinateReferenceSystem('EPSG:4326')
try:
    _ICON_CIRCLE = QgsRubberBand.IconType.ICON_CIRCLE
except AttributeError:
    _ICON_CIRCLE = getattr(QgsRubberBand, 'ICON_CIRCLE')


class PanoramaPointTool(QgsMapTool):
    """Place panorama points: a click on empty map adds one, and a drag that
    starts on a point moves it. Right click or Esc ends.

    points() returns the current points, (lon, lat) in WGS84. on_add(point)
    reports a click, on_move(index, point) a finished drag, and on_done()
    the end."""

    GRAB_PX = 10
    DRAG_THRESHOLD_PX = 4

    def __init__(self, canvas, points, on_add, on_move, on_done):
        super().__init__(canvas)
        self.setObjectName(FLYPATH_TOOL_NAME)
        self._points = points
        self._on_add = on_add
        self._on_move = on_move
        self._on_done = on_done
        self._press = None              # (pos, index of the grabbed point or None)
        self._band = QgsRubberBand(canvas, Qgis.GeometryType.Point)
        self._band.setIcon(_ICON_CIRCLE)
        self._band.setIconSize(12)
        self._band.setColor(QColor('#FF1493'))
        self._band.setWidth(2)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _transform(self, to_wgs84):
        canvas_crs = self.canvas().mapSettings().destinationCrs()
        source, destination = (canvas_crs, _WGS84) if to_wgs84 else (_WGS84, canvas_crs)
        return QgsCoordinateTransform(source, destination, QgsProject.instance())

    def _to_wgs84(self, pos):
        point = self._transform(True).transform(self.toMapCoordinates(pos))
        return (point.x(), point.y())

    def point_at(self, pos):
        """The index of the point under pos (within a few pixels), or None."""
        transform = self._transform(False)
        best = None
        for index, (lon, lat) in enumerate(self._points()):
            screen = self.toCanvasCoordinates(transform.transform(QgsPointXY(lon, lat)))
            distance = (screen - pos).manhattanLength()
            if distance <= self.GRAB_PX and (best is None or distance < best[0]):
                best = (distance, index)
        return None if best is None else best[1]

    def canvasPressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = (QPoint(event.pos()), self.point_at(event.pos()))

    def _dragged(self, start, pos):
        return (pos - start).manhattanLength() > self.DRAG_THRESHOLD_PX

    def canvasMoveEvent(self, event):
        if self._press is None:
            grabbed = self.point_at(event.pos()) is not None
            self.setCursor(Qt.CursorShape.SizeAllCursor if grabbed else Qt.CursorShape.CrossCursor)
            return
        if self._press[1] is not None and self._dragged(self._press[0], event.pos()):
            self._band.setToGeometry(
                QgsGeometry.fromPointXY(QgsPointXY(*self._to_wgs84(event.pos()))), _WGS84)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._press = None
            self._on_done()
            return
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        (start, index), self._press = self._press, None
        self._band.reset(Qgis.GeometryType.Point)
        if index is not None:
            if self._dragged(start, event.pos()):
                self._on_move(index, self._to_wgs84(event.pos()))
            return                      # a click on a point leaves it as it is
        self._on_add(self._to_wgs84(event.pos()))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_done()

    def deactivate(self):
        self._press = None
        if self._band is not None:
            self.canvas().scene().removeItem(self._band)
            self._band = None
        super().deactivate()


class PanoramaMixin:
    """Panorama mission controls and planning for the FlyPath panel."""

    def _panorama_init_state(self):
        self._panorama_points = []            # [(lon, lat)] in WGS84, in flight order
        self._panorama_layer_id = None
        self._panorama_tool = None
        self._panorama_prev_tool = None
        self._panorama_saved_max_waypoints = None   # mapping limit, restored on leaving

    # ── Building the controls ─────────────────────────────────────────────

    def _build_panorama_area_row(self, form):
        """Mode, point read-out and Place rows for the Survey Area group.

        The mode (Points or Area) comes first, above Source, because it decides
        whether points are clicked or an area is drawn."""
        dialog = sys.modules[type(self).__module__]   # the panel's segmented buttons
        self.panoramaPointsRadio = dialog._SegmentButton('Points')
        self.panoramaAreaRadio = dialog._SegmentButton('Area')
        self.panoramaPointsRadio.setChecked(True)
        self._panoramaModeGroup = QButtonGroup(self)
        for button in (self.panoramaPointsRadio, self.panoramaAreaRadio):
            self._panoramaModeGroup.addButton(button)
        self._tip(self.panoramaPointsRadio,
            'Points: a panorama at every point you click, flown in the order you '
            'click them; one point is a single panorama. Drag a point to move it, '
            'and Undo removes the last one. Points need some room between them, '
            'so the laps around them do not overlap.')
        self._tip(self.panoramaAreaRadio,
            'Area: draw or choose a survey area, and FlyPath spreads panorama '
            'points over it on a hexagon grid (like Spexi), flown row by row.')
        self._panoramaModeRow = QWidget()
        mode_layout = QHBoxLayout(self._panoramaModeRow)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(dialog._segmented(self.panoramaPointsRadio, self.panoramaAreaRadio))
        mode_layout.addStretch()
        source_row, _role = form.getWidgetPosition(self._sourceRow)
        form.insertRow(source_row, 'Panorama', self._panoramaModeRow)
        self._set_row_visible(form, self._panoramaModeRow, False)

        self.panoramaPointLabel = QLabel(NO_POINT_TEXT)
        self.panoramaPointLabel.setObjectName('selectionInfo')
        self._tip(self.panoramaPointLabel,
            'Where the panoramas are taken: the point, or how many points.')
        form.addRow('Points', self.panoramaPointLabel)
        self._set_row_visible(form, self.panoramaPointLabel, False)

        self.placePointBtn = QPushButton(PLACE_TEXT)
        self.placePointBtn.setObjectName('placePointBtn')
        self.placePointBtn.setCheckable(True)
        self._tip(self.placePointBtn,
            'Click the map where each panorama should be taken, in the order to '
            'fly them. Drag a point to move it. Right click or Escape finishes.')
        self.undoPointBtn = QPushButton('↶ Undo')
        self.undoPointBtn.setObjectName('undoPointBtn')
        self._tip(self.undoPointBtn, 'Remove the last point added.')
        self.undoPointBtn.setVisible(False)
        self.removePointBtn = QPushButton('✕ Remove')
        self.removePointBtn.setObjectName('removePointBtn')
        self._tip(self.removePointBtn, 'Remove every panorama point and its waypoints.')
        self.removePointBtn.setVisible(False)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.placePointBtn, 2)
        layout.addWidget(self.undoPointBtn, 1)
        layout.addWidget(self.removePointBtn, 1)
        for button in (self.placePointBtn, self.undoPointBtn, self.removePointBtn):
            button.setMinimumHeight(28)     # same height as the 2D Draw buttons
        form_rows.fit_to_row(self.placePointBtn, self.undoPointBtn, self.removePointBtn)
        self._panoramaPlaceRow = row
        form.addRow(row)
        self._set_row_visible(form, row, False)

    def _build_panorama_flight_rows(self, form):
        """Overlap and nadir rows for the Flight Parameters."""
        self.panoramaOverlapSpin = QSpinBox()
        self.panoramaOverlapSpin.setRange(20, 70)
        self.panoramaOverlapSpin.setValue(DEFAULT_PANORAMA_OVERLAP)
        self.panoramaOverlapSpin.setSingleStep(5)
        self.panoramaOverlapSpin.setSuffix(' %')
        self._tip(self.panoramaOverlapSpin,
            'How much neighbouring photos overlap, around each row and between '
            'rows. 30% suits most stitching software; more overlap takes more '
            'photos.')
        form.addRow('Photo Overlap', self.panoramaOverlapSpin)

        self.panoramaNadirCheck = QCheckBox('Straight-down photo')
        self.panoramaNadirCheck.setChecked(True)
        self._tip(self.panoramaNadirCheck,
            'End each panorama with one photo straight down over its point, so '
            'the ground under the drone is in the panorama too.')
        form.addRow('Nadir Photo', self.panoramaNadirCheck)

        self.panoramaSpacingSpin = QSpinBox()
        self.panoramaSpacingSpin.setRange(0, 5000)
        self.panoramaSpacingSpin.setSingleStep(10)
        self.panoramaSpacingSpin.setSuffix(' m')
        self.panoramaSpacingSpin.setValue(0)
        self._tip(self.panoramaSpacingSpin,
            'Distance between neighbouring panorama points in Area mode. Auto '
            'uses the altitude. Closer points take more photos and flight time.')
        form.addRow('Point Spacing', self.panoramaSpacingSpin)
        for widget in (self.panoramaOverlapSpin, self.panoramaNadirCheck, self.panoramaSpacingSpin):
            self._set_row_visible(form, widget, False)
        self._refresh_panorama_spacing_hint()

    def _panorama_connect_signals(self):
        self.panoramaAreaRadio.toggled.connect(self._on_panorama_area_toggled)
        self.panoramaSpacingSpin.valueChanged.connect(self._on_param_changed)
        self.altitudeSpin.valueChanged.connect(self._refresh_panorama_spacing_hint)
        self.placePointBtn.toggled.connect(self._on_place_point_toggled)
        self.undoPointBtn.clicked.connect(self._on_undo_panorama_point)
        self.removePointBtn.clicked.connect(self._on_remove_panorama_point)
        self.panoramaOverlapSpin.valueChanged.connect(self._on_param_changed)
        self.panoramaNadirCheck.toggled.connect(self._on_param_changed)

    # ── Layout ────────────────────────────────────────────────────────────

    def _apply_panorama_layout(self, panorama):
        """Show the panorama controls and hide the mapping ones.

        Called with False before the other kinds' layouts, which restore
        their own rows, and with True after them."""
        area = panorama and self._panorama_area()
        for widget in (self._panoramaModeRow, self.panoramaPointLabel):
            self._set_row_visible(self._area_form, widget, panorama)
        self._set_row_visible(self._area_form, self._panoramaPlaceRow, panorama and not area)
        for widget in (self.panoramaOverlapSpin, self.panoramaNadirCheck):
            self._set_row_visible(self._flight_form, widget, panorama)
        self._set_row_visible(self._flight_form, self.panoramaSpacingSpin, area)
        if area:
            self._refresh_panorama_spacing_hint()
        self._set_row_visible(self._area_form, self.areaLabel, area or not panorama)
        # Every photo has its own waypoint, so the capture choice is hidden and
        # the row offers the Flight Path alone.
        self._captureToggle.setVisible(not panorama)
        self._pathCaption.setVisible(not panorama)
        label = self._row_label(self._mission_form, self._captureRow)
        if label is not None:
            label.setText('Flight Path' if panorama else 'Capture')
        self._routeTitle.setVisible(True)
        if not panorama:
            return
        # Area mode takes its polygon from the usual Draw, Layer or Selection source.
        self._set_row_visible(self._area_form, self._sourceRow, area)
        self._set_row_visible(self._area_form, self._sourceStack, area)
        self._set_row_visible(self._flight_form, self.gsdSpin, False)
        # Flights split between panoramas by Max Waypoints and the battery,
        # so Split Missions, terrain follow and reversing do not apply.
        for widget in (self.splitCheck, self.splitSpin):
            self._set_row_visible(self._organizer_form, widget, False)
        self.terrainFollowCheck.setVisible(False)
        self.reverseRouteCheck.setVisible(False)
        self._routeTitle.setVisible(False)      # no route options apply
        self._pathRow.setVisible(True)
        self._takeoffGroup.setVisible(False)
        blocked = self.altitudeSpin.blockSignals(True)
        self.altitudeSpin.setMinimum(PANORAMA_MIN_ALTITUDE_M)
        self.altitudeSpin.blockSignals(blocked)

    def _switch_panorama_mode(self, panorama):
        """Change values when the mission kind switches into or out of panorama."""
        if panorama:
            self.terrainFollowCheck.setChecked(False)
            self.reverseRouteCheck.setChecked(False)
            self.splitCheck.setChecked(False)
            for button in (self.showTakeoffZoneBtn, self.showContoursBtn):
                if button.isCheckable() and button.isChecked():
                    button.setChecked(False)
        blocked = self.maxWaypointsSpin.blockSignals(True)
        if panorama and self._panorama_saved_max_waypoints is None:
            self._panorama_saved_max_waypoints = self.maxWaypointsSpin.value()
            self.maxWaypointsSpin.setValue(DEFAULT_PANORAMA_MAX_WAYPOINTS)
        elif not panorama and self._panorama_saved_max_waypoints is not None:
            self.maxWaypointsSpin.setValue(self._panorama_saved_max_waypoints)
            self._panorama_saved_max_waypoints = None
        self.maxWaypointsSpin.blockSignals(blocked)
        if not panorama:
            self._clear_panorama_points()

    def _apply_panorama_capabilities(self):
        """No overlap rows of the mapping kinds, nor a DEM, apply here."""
        self._set_row_visible(self._flight_form, self.sideOverlapSpin, False)
        self._set_row_visible(self._flight_form, self.frontOverlapStack, False)
        self._set_row_visible(self._area_form, self.demCombo, False)

    def _refresh_panorama_spacing_hint(self, *_args):
        """Show Auto's distance at this altitude."""
        auto = planning_adapter.PANORAMA_AUTO_SPACING_PER_ALTITUDE * self.altitudeSpin.value()
        self.panoramaSpacingSpin.setSpecialValueText(f'Auto ({auto:.0f} m)')

    def _panorama_area(self):
        return self.panoramaAreaRadio.isChecked()

    def _on_panorama_area_toggled(self, area):
        """Points and an area are different inputs: switching clears the one
        in use, then shows the controls of the other."""
        if self._mission_kind() != 'panorama':
            return
        self._on_clear_preview(reset_area=True)
        self._apply_kind_layout()
        self._apply_mission_type_capabilities()
        self._refresh_panorama_controls()

    # ── Point tool ────────────────────────────────────────────────────────

    def _on_place_point_toggled(self, checked):
        canvas = self.iface.mapCanvas()
        if not checked:
            self._leave_panorama_tool()
            return
        if self._panorama_tool is None:
            self._panorama_prev_tool = previous_tool(canvas)
        self._panorama_tool = PanoramaPointTool(
            canvas, lambda: self._panorama_points, self._on_panorama_point_placed,
            self._on_panorama_point_moved, self._leave_panorama_tool)
        canvas.setMapTool(self._panorama_tool)
        self.placePointBtn.setText(PLACING_TEXT)
        self._set_info('Click where each panorama should be taken, in the order to fly '
                       'them. Drag a point to move it. Right click or Escape finishes.')

    def _leave_panorama_tool(self):
        """Turn the point tool off and restore the earlier map tool."""
        canvas = self.iface.mapCanvas()
        tool, self._panorama_tool = self._panorama_tool, None
        if tool is not None:
            if self._panorama_prev_tool is not None:
                canvas.setMapTool(self._panorama_prev_tool)
            elif is_flypath_tool(canvas.mapTool()):
                canvas.unsetMapTool(canvas.mapTool())
            self._set_info(_INFO_IDLE)
        self._panorama_prev_tool = None
        blocked = self.placePointBtn.blockSignals(True)
        self.placePointBtn.setChecked(False)
        self.placePointBtn.setText(PLACE_TEXT)
        self.placePointBtn.blockSignals(blocked)

    def _on_panorama_point_placed(self, point):
        """A click adds a point after the others."""
        self._apply_panorama_points(self._panorama_points + [point], 'Point Not Added')

    def _on_panorama_point_moved(self, index, point):
        """A drag moves one point; the others and the order stay."""
        points = list(self._panorama_points)
        points[index] = point
        self._apply_panorama_points(points, 'Point Not Moved')

    def _apply_panorama_points(self, points, refused_title):
        """Use these points and preview them, unless they are too close."""
        problem = self._panorama_points_problem(points)
        if problem:
            self._set_info(problem)
            QMessageBox.information(self, refused_title, problem)
            return
        self.set_panorama_points(points)
        self._on_param_changed()
        if not self._preview_layer_ids:
            self._on_preview()

    def _panorama_points_problem(self, points):
        """Why the engine refuses these points (too close), or None."""
        try:
            planning_adapter.plan(self._panorama_request_from_ui(points))
        except planning_adapter.PlanningError as exc:
            if exc.code == 'points_too_close':
                return exc.message + ' Place it a little further away.'
        except ValueError:
            pass                            # the preview reports drone problems
        return None

    def _on_undo_panorama_point(self):
        if not self._panorama_points:
            return
        points = self._panorama_points[:-1]
        if not points:
            self._on_remove_panorama_point()
            return
        self.set_panorama_points(points)
        self._on_param_changed()

    def _on_remove_panorama_point(self):
        """Remove every point and its waypoints, like Remove in 2D Draw."""
        self._on_clear_preview(reset_area=True)

    def set_panorama_points(self, points):
        """Set the panorama points (WGS84, flight order) and draw their markers."""
        self._panorama_points = [(float(lon), float(lat)) for lon, lat in points]
        self._show_panorama_points()
        self._refresh_panorama_controls()

    def set_panorama_point(self, longitude, latitude):
        """Set a single panorama point (WGS84)."""
        self.set_panorama_points([(longitude, latitude)])

    def _refresh_panorama_controls(self):
        points = self._panorama_points
        if self._panorama_area():
            # The engine places the points; the label counts them once planned.
            self.removePointBtn.setVisible(False)
            self.undoPointBtn.setVisible(False)
            if not points:
                self.panoramaPointLabel.setText('— draw or choose an area —')
            return
        if not points:
            text = NO_POINT_TEXT
        elif len(points) == 1:
            text = f'{points[0][1]:.5f}, {points[0][0]:.5f}'     # ~1 m
        else:
            text = f'{len(points)} points'
        self.panoramaPointLabel.setText(text)
        self.removePointBtn.setVisible(bool(points))
        self.undoPointBtn.setVisible(bool(points))

    def _show_panorama_points(self, points=None):
        """Mark the points, numbered when there are several: the clicked ones,
        or those the engine placed over an area."""
        points = self._panorama_points if points is None else points
        self._remove_panorama_layer()
        if not points:
            return
        layer = QgsVectorLayer('Point?crs=EPSG:4326&field=label:string',
                               'FlyPath — Panorama Points', 'memory')
        layer.setCustomProperty('flypath_internal', True)
        features = []
        for number, point in enumerate(points, 1):
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*point)))
            feature.setAttribute('label', str(number) if len(points) > 1 else '')
            features.append(feature)
        layer.dataProvider().addFeatures(features)
        layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({
            'name': 'cross2', 'color': '#FF8A78', 'outline_color': '#FF8A78',
            'outline_width': '0.8', 'size': '6',
        }))
        # Multiple points are numbered in flight order.
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
        settings.setFormat(text)
        settings.xOffset = 4
        settings.yOffset = -4
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        preview_layers.register(layer, kind='panorama_point')
        self._panorama_layer_id = layer.id()
        self.iface.mapCanvas().refresh()

    def _remove_panorama_layer(self):
        if self._panorama_layer_id:
            preview_layers.remove([self._panorama_layer_id])
        self._panorama_layer_id = None

    def _clear_panorama_points(self):
        self._leave_panorama_tool()
        self._panorama_points = []
        self._remove_panorama_layer()
        self._refresh_panorama_controls()

    def _show_planned_panorama_points(self, result):
        """In Area mode, mark the points the engine placed and count them."""
        if not self._panorama_area():
            return
        centres = [(point['centre']['longitude_deg'], point['centre']['latitude_deg'])
                   for point in result['panorama']['points']]
        self._show_panorama_points(centres)
        self.panoramaPointLabel.setText(
            '1 point' if len(centres) == 1 else f'{len(centres)} points'
            f" · {result['panorama']['point_spacing_m']:.0f} m apart")

    def _has_panorama_point(self, silent=False):
        if self._panorama_area():
            if self._survey_polygon is None or self._survey_polygon_crs is None:
                if not silent:
                    QMessageBox.information(
                        self, 'No Panorama Area',
                        'Draw a polygon on the map or select a polygon layer first.')
                return False
            return True
        if not self._panorama_points:
            if not silent:
                QMessageBox.information(
                    self, 'No Panorama Point',
                    'Click Place Points on Map, then click where the panorama should be taken.')
            return False
        return True

    # ── Planning ──────────────────────────────────────────────────────────

    def _panorama_request_from_ui(self, points=None):
        drone = registry.get(self.droneModelCombo.currentText())
        if drone.category != 'consumer' or not drone.website_code:
            raise ValueError('Panorama missions need a DJI Fly drone; %s is not supported.'
                             % drone.name)
        if self._panorama_area() and points is None:
            area = {'survey_area': survey_geometry.planning_area(
                        self._survey_polygon, self._survey_polygon_crs),
                    'point_spacing_m': self.panoramaSpacingSpin.value() or None}
        else:
            area = {'points': self._panorama_points if points is None else points}
        return planning_adapter.build_panorama_request(
            **area,
            drone_profile_id=drone.website_code,
            altitude_m=self.altitudeSpin.value(),
            speed_m_s=self.speedSpin.value(),
            overlap_ratio=self.panoramaOverlapSpin.value() / 100.0,
            include_nadir=self.panoramaNadirCheck.isChecked(),
            finish_action=self.finishActionCombo.currentText(),
            max_waypoints_per_flight=self.maxWaypointsSpin.value(),
            turn_style='curved' if self._path_curved() else 'straight',
        )
