"""Panorama missions: photos all around one point, for a stitched panorama.

A mixin for FlyPathDialog, in the same style as OrbitMixin. It owns the
panorama controls, the point tool, the point marker and the request
translation. The dialog's shared planning flow runs the request in the engine
(plan_panorama), which flies small outward-facing laps around the point, one
per camera row, because DJI Fly missions cannot turn the drone in place.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QWidget,
)
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsGeometry,
    QgsMarkerSymbol, QgsPointXY, QgsProject, QgsVectorLayer,
)
from qgis.gui import QgsMapTool

from .map_tools import FLYPATH_TOOL_NAME, is_flypath_tool, previous_tool

try:
    from . import form_rows, planning_adapter, preview_layers
    from .hardware import registry
except ImportError:  # standalone tests import modules as top-level packages
    import form_rows
    import planning_adapter
    import preview_layers
    from hardware import registry


PANORAMA_LABEL = 'Panorama'
DEFAULT_PANORAMA_OVERLAP = 30   # % between neighbouring photos, as DJI's own panoramas
PANORAMA_MIN_ALTITUDE_M = 10.0
NO_POINT_TEXT = '— no panorama point —'
PLACE_TEXT = 'Place Point on Map'
_INFO_IDLE = 'ⓘ  Hover over any field to see what it does.'
_WGS84 = QgsCoordinateReferenceSystem('EPSG:4326')


class PanoramaPointTool(QgsMapTool):
    """Place the panorama point: a click sets it; right click or Esc cancels.

    on_done((lon, lat)) reports the point in WGS84, on_done(None) a cancel."""

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
        self._panorama_point = None           # (lon, lat) in WGS84
        self._panorama_layer_id = None
        self._panorama_tool = None
        self._panorama_prev_tool = None

    # ── Building the controls ─────────────────────────────────────────────

    def _build_panorama_area_row(self, form):
        """Point and Place rows for the Survey Area group (replace Source)."""
        self.panoramaPointLabel = QLabel(NO_POINT_TEXT)
        self.panoramaPointLabel.setObjectName('selectionInfo')
        self._tip(self.panoramaPointLabel,
            'Latitude and longitude of the point the panorama is taken from.')
        form.addRow('Panorama Point', self.panoramaPointLabel)
        self._set_row_visible(form, self.panoramaPointLabel, False)

        self.placePointBtn = QPushButton(PLACE_TEXT)
        self.placePointBtn.setObjectName('placePointBtn')
        self.placePointBtn.setCheckable(True)
        self._tip(self.placePointBtn,
            'Click the map where the panorama should be taken. Click again to '
            'move the point. Right click or Escape finishes.')
        self.removePointBtn = QPushButton('✕ Remove')
        self.removePointBtn.setObjectName('removePointBtn')
        self._tip(self.removePointBtn, 'Remove the panorama point and its waypoints.')
        self.removePointBtn.setVisible(False)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.placePointBtn, 2)
        layout.addWidget(self.removePointBtn, 1)
        for button in (self.placePointBtn, self.removePointBtn):
            button.setMinimumHeight(28)     # same height as the 2D Draw buttons
        form_rows.fit_to_row(self.placePointBtn, self.removePointBtn)
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
            'End with one photo straight down over the point, so the ground '
            'under the drone is in the panorama too.')
        form.addRow('Nadir Photo', self.panoramaNadirCheck)
        for widget in (self.panoramaOverlapSpin, self.panoramaNadirCheck):
            self._set_row_visible(form, widget, False)

    def _panorama_connect_signals(self):
        self.placePointBtn.toggled.connect(self._on_place_point_toggled)
        self.removePointBtn.clicked.connect(self._on_remove_panorama_point)
        self.panoramaOverlapSpin.valueChanged.connect(self._on_param_changed)
        self.panoramaNadirCheck.toggled.connect(self._on_param_changed)

    # ── Layout ────────────────────────────────────────────────────────────

    def _apply_panorama_layout(self, panorama):
        """Show the panorama controls and hide the mapping ones.

        Called with False before the other kinds' layouts, which restore
        their own rows, and with True after them."""
        self._set_row_visible(self._area_form, self.panoramaPointLabel, panorama)
        self._set_row_visible(self._area_form, self._panoramaPlaceRow, panorama)
        for widget in (self.panoramaOverlapSpin, self.panoramaNadirCheck):
            self._set_row_visible(self._flight_form, widget, panorama)
        # Every photo has its own waypoint, so the capture choice is hidden.
        self._set_row_visible(self._mission_form, self._captureRow, not panorama)
        # One short flight: no splitting, terrain follow or reversing. The
        # other kinds' layouts set the split rows, but not Max Waypoints.
        self._set_row_visible(self._organizer_form, self.maxWaypointsSpin, not panorama)
        if not panorama:
            return
        self._set_row_visible(self._area_form, self._sourceRow, False)
        self._set_row_visible(self._area_form, self._sourceStack, False)
        self._set_row_visible(self._flight_form, self.gsdSpin, False)
        for widget in (self.splitCheck, self.splitSpin):
            self._set_row_visible(self._organizer_form, widget, False)
        self.terrainFollowCheck.setVisible(False)
        self.reverseRouteCheck.setVisible(False)
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
        else:
            self._clear_panorama_point()

    def _apply_panorama_capabilities(self):
        """No overlap rows of the mapping kinds, nor a DEM, apply here."""
        self._set_row_visible(self._flight_form, self.sideOverlapSpin, False)
        self._set_row_visible(self._flight_form, self.frontOverlapStack, False)
        self._set_row_visible(self._area_form, self.demCombo, False)

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
        self.placePointBtn.setText('Click the point…')
        self._set_info('Click where the panorama should be taken. Click again to move '
                       'it. Right click or Escape finishes.')

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
        """A click moves the point and previews it; a cancel ends placing."""
        if point is None:
            self._leave_panorama_tool()
            return
        self.set_panorama_point(*point)
        self._on_param_changed()
        if not self._preview_layer_ids:
            self._on_preview()

    def _on_remove_panorama_point(self):
        """Remove the point and its waypoints, like Remove in 2D Draw."""
        self._on_clear_preview(reset_area=True)

    def set_panorama_point(self, longitude, latitude):
        """Set the panorama point (WGS84) and draw its marker."""
        self._panorama_point = (float(longitude), float(latitude))
        self.panoramaPointLabel.setText(f'{latitude:.5f}, {longitude:.5f}')   # ~1 m
        self._show_panorama_point()

    def _show_panorama_point(self):
        self._remove_panorama_layer()
        if self._panorama_point is None:
            return
        layer = QgsVectorLayer('Point?crs=EPSG:4326', 'FlyPath — Panorama Point', 'memory')
        layer.setCustomProperty('flypath_internal', True)
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*self._panorama_point)))
        layer.dataProvider().addFeatures([feature])
        layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({
            'name': 'cross2', 'color': '#FF8A78', 'outline_color': '#FF8A78',
            'outline_width': '0.8', 'size': '6',
        }))
        preview_layers.register(layer, kind='panorama_point')
        self._panorama_layer_id = layer.id()
        self.removePointBtn.setVisible(True)
        self.iface.mapCanvas().refresh()

    def _remove_panorama_layer(self):
        if self._panorama_layer_id:
            preview_layers.remove([self._panorama_layer_id])
        self._panorama_layer_id = None

    def _clear_panorama_point(self):
        self._leave_panorama_tool()
        self._panorama_point = None
        self.panoramaPointLabel.setText(NO_POINT_TEXT)
        self._remove_panorama_layer()
        self.removePointBtn.setVisible(False)

    def _has_panorama_point(self, silent=False):
        if self._panorama_point is None:
            if not silent:
                QMessageBox.information(
                    self, 'No Panorama Point',
                    'Click Place Point on Map, then click where the panorama should be taken.')
            return False
        return True

    # ── Planning ──────────────────────────────────────────────────────────

    def _panorama_request_from_ui(self):
        drone = registry.get(self.droneModelCombo.currentText())
        if drone.category != 'consumer' or not drone.website_code:
            raise ValueError('Panorama missions need a DJI Fly drone; %s is not supported.'
                             % drone.name)
        return planning_adapter.build_panorama_request(
            centre=self._panorama_point,
            drone_profile_id=drone.website_code,
            altitude_m=self.altitudeSpin.value(),
            speed_m_s=self.speedSpin.value(),
            overlap_ratio=self.panoramaOverlapSpin.value() / 100.0,
            include_nadir=self.panoramaNadirCheck.isChecked(),
            finish_action=self.finishActionCombo.currentText(),
        )
