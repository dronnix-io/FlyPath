"""Panorama missions: photos all around one or more points, for stitched panoramas.

A mixin for FlyPathDialog, in the same style as OrbitMixin. It owns the
panorama controls, the point tool, the point markers and the request
translation. The dialog's shared planning flow runs the request in the engine
(plan_panorama), which flies small outward-facing laps around each point, one
per camera row, because DJI Fly missions cannot turn the drone in place.

Single places one point, and a click moves it. Multiple adds a point with each
click, numbered in flight order; Undo removes the last one. Area takes a survey
polygon from the usual Draw, Layer or Selection source, and the engine places
the points on a hexagon grid inside it, by default three altitudes apart.
"""

import sys

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QButtonGroup, QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox,
    QWidget,
)
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsGeometry,
    QgsMarkerSymbol, QgsPalLayerSettings, QgsPointXY, QgsProject, QgsTextBufferSettings,
    QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
)
from qgis.gui import QgsMapTool

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
PLACE_TEXT = 'Place Point on Map'
ADD_TEXT = 'Add Points on Map'
_INFO_IDLE = 'ⓘ  Hover over any field to see what it does.'
_WGS84 = QgsCoordinateReferenceSystem('EPSG:4326')


class PanoramaPointTool(QgsMapTool):
    """Place panorama points: each click reports one; right click or Esc ends.

    on_done((lon, lat)) reports a point in WGS84, on_done(None) the end."""

    def __init__(self, canvas, on_done):
        super().__init__(canvas)
        self.setObjectName(FLYPATH_TOOL_NAME)
        self._on_done = on_done
        self.setCursor(Qt.CursorShape.CrossCursor)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._on_done(None)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        transform = QgsCoordinateTransform(
            self.canvas().mapSettings().destinationCrs(), _WGS84, QgsProject.instance())
        point = transform.transform(self.toMapCoordinates(event.pos()))
        self._on_done((point.x(), point.y()))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_done(None)


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

        The mode (Single, Multiple or Area) comes first, above Source, because
        it decides whether points are clicked or an area is drawn."""
        dialog = sys.modules[type(self).__module__]   # the panel's segmented buttons
        self.panoramaSingleRadio = dialog._SegmentButton('Single')
        self.panoramaMultipleRadio = dialog._SegmentButton('Multiple')
        self.panoramaAreaRadio = dialog._SegmentButton('Area')
        self.panoramaSingleRadio.setChecked(True)
        self._panoramaModeGroup = QButtonGroup(self)
        for button in (self.panoramaSingleRadio, self.panoramaMultipleRadio, self.panoramaAreaRadio):
            self._panoramaModeGroup.addButton(button)
        self._tip(self.panoramaSingleRadio,
            'Single: one panorama. A click on the map places the point, and '
            'another click moves it.')
        self._tip(self.panoramaMultipleRadio,
            'Multiple: a panorama at every point you click, flown in the order '
            'you click them. Undo removes the last point. Points need some room '
            'between them, so the laps around them do not overlap.')
        self._tip(self.panoramaAreaRadio,
            'Area: draw or choose a survey area, and FlyPath spreads panorama '
            'points over it on a hexagon grid (like Spexi), flown row by row.')
        self._panoramaModeRow = QWidget()
        mode_layout = QHBoxLayout(self._panoramaModeRow)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(dialog._segmented(
            self.panoramaSingleRadio, self.panoramaMultipleRadio, self.panoramaAreaRadio))
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
            'Click the map where a panorama should be taken. Right click or '
            'Escape finishes.')
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
        self.panoramaSpacingSpin.setSpecialValueText('Auto (3 × altitude)')
        self.panoramaSpacingSpin.setValue(0)
        self._tip(self.panoramaSpacingSpin,
            'Distance between neighbouring panorama points in Area mode. Auto '
            'uses three times the altitude: each panorama sees the ground well '
            'out to about 1.7 times the altitude, so points this far apart cover '
            'the area without gaps. Closer points give sharper views of the '
            'ground between them, but more points and flight time.')
        form.addRow('Point Spacing', self.panoramaSpacingSpin)
        for widget in (self.panoramaOverlapSpin, self.panoramaNadirCheck, self.panoramaSpacingSpin):
            self._set_row_visible(form, widget, False)

    def _panorama_connect_signals(self):
        self.panoramaMultipleRadio.toggled.connect(self._on_panorama_mode_changed)
        self.panoramaAreaRadio.toggled.connect(self._on_panorama_area_toggled)
        self.panoramaSpacingSpin.valueChanged.connect(self._on_param_changed)
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
        self._set_row_visible(self._area_form, self.areaLabel, area or not panorama)
        # Every photo has its own waypoint, so the capture choice is hidden.
        self._set_row_visible(self._mission_form, self._captureRow, not panorama)
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
        self._takeoffGroup.setVisible(False)
        blocked = self.altitudeSpin.blockSignals(True)
        self.altitudeSpin.setMinimum(PANORAMA_MIN_ALTITUDE_M)
        self.altitudeSpin.blockSignals(blocked)

    def _switch_panorama_mode(self, panorama):
        """Change values when the mission kind switches into or out of panorama."""
        if panorama:
            self.pathCurvedRadio.setChecked(True)   # DJI Fly keeps curved paths on re-save
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

    def _panorama_multiple(self):
        return self.panoramaMultipleRadio.isChecked()

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

    def _on_panorama_mode_changed(self, multiple):
        """Single keeps only the first point; both modes keep placing."""
        self.placePointBtn.setText(self._place_text())
        if not multiple and len(self._panorama_points) > 1:
            self._panorama_points = self._panorama_points[:1]
            self._show_panorama_points()
            self._on_param_changed()
        self._refresh_panorama_controls()

    def _place_text(self):
        if self._panorama_tool is not None:
            return 'Click the points…' if self._panorama_multiple() else 'Click the point…'
        return ADD_TEXT if self._panorama_multiple() else PLACE_TEXT

    # ── Point tool ────────────────────────────────────────────────────────

    def _on_place_point_toggled(self, checked):
        canvas = self.iface.mapCanvas()
        if not checked:
            self._leave_panorama_tool()
            return
        if self._panorama_tool is None:
            self._panorama_prev_tool = previous_tool(canvas)
        self._panorama_tool = PanoramaPointTool(canvas, self._on_panorama_point_placed)
        canvas.setMapTool(self._panorama_tool)
        self.placePointBtn.setText(self._place_text())
        self._set_info(
            'Click each point where a panorama should be taken, in the order to fly '
            'them. Right click or Escape finishes.' if self._panorama_multiple() else
            'Click where the panorama should be taken. Click again to move it. '
            'Right click or Escape finishes.')

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
        self.placePointBtn.setText(self._place_text())
        self.placePointBtn.blockSignals(blocked)

    def _on_panorama_point_placed(self, point):
        """A click adds a point (Multiple) or moves it (Single); a cancel ends placing."""
        if point is None:
            self._leave_panorama_tool()
            return
        points = (self._panorama_points + [point] if self._panorama_multiple()
                  else [point])
        problem = self._panorama_points_problem(points)
        if problem:
            self._set_info(problem)
            QMessageBox.information(self, 'Point Not Added', problem)
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
                return exc.message + ' Click a little further away.'
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
        self.undoPointBtn.setVisible(self._panorama_multiple() and bool(points))

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
                    'Click Place Point on Map, then click where the panorama should be taken.')
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
        )
