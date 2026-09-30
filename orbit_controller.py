"""Orbit (3D model) missions: pick a centre, fly one ring facing it.

A mixin for FlyPathDialog, in the same style as SurveyLifecycleMixin. It owns
the orbit controls, the centre pick tool, the centre marker layer and the
orbit planning call. The dialog routes here wherever the mission kind is
'orbit'; planning itself runs in the shared engine (plan_orbit).
"""

from qgis.PyQt.QtWidgets import (
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSpinBox, QWidget,
)
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsGeometry,
    QgsMarkerSymbol, QgsPointXY, QgsProject, QgsVectorLayer,
)
from qgis.gui import QgsMapToolEmitPoint

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


class OrbitMixin:
    """Orbit mission controls and planning for the FlyPath panel."""

    def _orbit_init_state(self):
        self._orbit_centre = None           # (lon, lat) in WGS84
        self._orbit_centre_layer_id = None
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
            'Click the object to orbit on the map. The drone flies a circle '
            'around this point, always facing it.')
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
            self._orbit_tool = QgsMapToolEmitPoint(canvas)
            self._orbit_tool.canvasClicked.connect(self._on_orbit_centre_clicked)
            canvas.setMapTool(self._orbit_tool)
            self.pickCentreBtn.setText('Click the centre on the map…')
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

    def _on_orbit_centre_clicked(self, point, _button):
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        to_wgs84 = QgsCoordinateTransform(
            canvas_crs, QgsCoordinateReferenceSystem('EPSG:4326'), QgsProject.instance())
        wgs84 = to_wgs84.transform(point)
        self.set_orbit_centre(wgs84.x(), wgs84.y())
        self.pickCentreBtn.setChecked(False)    # turns the pick tool off
        self._on_param_changed()

    def set_orbit_centre(self, longitude, latitude):
        """Set the orbit centre (WGS84) and draw its marker."""
        self._orbit_centre = (float(longitude), float(latitude))
        self.orbitCentreLabel.setText(f'{latitude:.6f}, {longitude:.6f}')
        self._show_orbit_centre()

    def _show_orbit_centre(self):
        self._remove_orbit_centre_layer()
        if self._orbit_centre is None:
            return
        layer = QgsVectorLayer('Point?crs=EPSG:4326', 'FlyPath — Orbit Centre', 'memory')
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
        if self._orbit_centre_layer_id:
            preview_layers.remove([self._orbit_centre_layer_id])
            self._orbit_centre_layer_id = None

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
