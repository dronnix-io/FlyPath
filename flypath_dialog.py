import math
import os

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QScrollArea, QFrame,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QSpinBox, QDoubleSpinBox, QSizePolicy,
    QMessageBox, QFileDialog,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from qgis.core import (
    QgsProject,
    QgsWkbTypes,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsFillSymbol,
    QgsRuleBasedRenderer,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
)
from qgis.PyQt.QtGui import QColor, QFont

from .map_tools import PolygonDrawTool
from .grid_planner import generate_flight_grid, find_optimal_direction
from .wpml_writer import write_kmz


# ── Drone / camera specifications ─────────────────────────────────────────
DRONE_SPECS = {
    'DJI Mini 3': {
        'sensor_width_mm':  9.6,
        'sensor_height_mm': 7.2,
        'focal_length_mm':  6.9,
        'image_width_px':   4000,
        'image_height_px':  3000,
        'max_speed_ms':     16.0,
        'battery_time_min': 38,
        'info': '1/1.3" CMOS  ·  12 MP  ·  24 mm equiv',
    },
    'DJI Mini 3 Pro': {
        'sensor_width_mm':  9.6,
        'sensor_height_mm': 7.2,
        'focal_length_mm':  6.9,
        'image_width_px':   4000,
        'image_height_px':  3000,
        'max_speed_ms':     16.0,
        'battery_time_min': 34,
        'info': '1/1.3" CMOS  ·  12 MP  ·  24 mm equiv',
    },
    'DJI Mini 4 Pro': {
        'sensor_width_mm':  9.6,
        'sensor_height_mm': 7.2,
        'focal_length_mm':  6.9,
        'image_width_px':   4000,
        'image_height_px':  3000,
        'max_speed_ms':     16.0,
        'battery_time_min': 34,
        'info': '1/1.3" CMOS  ·  12 MP  ·  24 mm equiv',
    },
    'DJI Mini 5': {
        'sensor_width_mm':  9.6,
        'sensor_height_mm': 7.2,
        'focal_length_mm':  6.9,
        'image_width_px':   4000,
        'image_height_px':  3000,
        'max_speed_ms':     18.0,
        'battery_time_min': 35,
        'info': '1/1.3" CMOS  ·  12 MP  ·  24 mm equiv',
    },
}

# ── Dark stylesheet (Litchi-inspired) ─────────────────────────────────────
STYLESHEET = """
QWidget {
    background-color: #1E2128;
    color: #D0D0D0;
    font-size: 11px;
    font-family: "Segoe UI", Arial, sans-serif;
}
QGroupBox {
    border: 1px solid #3A3D45;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 6px;
    font-weight: bold;
    color: #7FB3E8;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
    background-color: #2A2D35;
    border: 1px solid #3A3D45;
    border-radius: 3px;
    padding: 3px 6px;
    color: #E0E0E0;
    selection-background-color: #2D6DB5;
}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #2D6DB5;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
    background-color: #3A3D45;
    border-radius: 0 3px 3px 0;
}
QComboBox::drop-down:hover { background-color: #4A4D55; }
QComboBox::down-arrow { image: url(ARROW_DOWN_PATH); width: 10px; height: 6px; }
QComboBox QAbstractItemView {
    background-color: #2A2D35;
    border: 1px solid #3A3D45;
    selection-background-color: #2D6DB5;
    color: #E0E0E0;
    outline: none;
}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button,       QSpinBox::down-button {
    background-color: #3A3D45; border: none; width: 16px;
}
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover,
QSpinBox::up-button:hover,       QSpinBox::down-button:hover {
    background-color: #4A4D55;
}
QPushButton {
    background-color: #2D6DB5;
    color: white; border: none; border-radius: 4px;
    padding: 5px 10px; font-weight: bold;
}
QPushButton:hover   { background-color: #3A7EC6; }
QPushButton:pressed { background-color: #1F5A9E; }
QPushButton#exportBtn {
    background-color: #F0A500; color: #1A1A1A; font-size: 12px;
}
QPushButton#exportBtn:hover   { background-color: #FFB520; }
QPushButton#exportBtn:pressed { background-color: #D09000; }
QPushButton#clearPreviewBtn {
    background-color: #3A3D45; color: #D0D0D0; font-weight: normal;
}
QPushButton#clearPreviewBtn:hover { background-color: #4A4D55; }
QPushButton#drawPolygonBtn {
    background-color: #1E3A1E; color: #80C880;
    border: 1px solid #2E5A2E; font-weight: bold;
}
QPushButton#drawPolygonBtn:hover   { background-color: #2A4D2A; }
QPushButton#drawPolygonBtn:checked {
    background-color: #2A6A2A; border: 1px solid #40A040; color: #AAFAAA;
}
QPushButton#autoDirectionBtn {
    background-color: #3A3D45; color: #D0D0D0;
    font-weight: normal; font-size: 10px; padding: 3px 6px;
}
QPushButton#autoDirectionBtn:hover { background-color: #4A4D55; }
QPushButton#removePolygonBtn {
    background-color: #5A2020; color: #FF8888;
    border: 1px solid #7A3030; border-radius: 3px;
    font-weight: bold; padding: 3px 6px;
}
QPushButton#removePolygonBtn:hover { background-color: #7A2525; color: #FFAAAA; }
QScrollArea { border: none; background-color: transparent; }
QScrollBar:vertical {
    background-color: #2A2D35; width: 7px; border-radius: 3px;
}
QScrollBar::handle:vertical {
    background-color: #4A4D55; border-radius: 3px; min-height: 20px;
}
QScrollBar::handle:vertical:hover  { background-color: #5A5D65; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLabel#gsdLabel, QLabel#areaLabel, QLabel#intervalLabel,
QLabel#flightTimeLabel, QLabel#distanceLabel, QLabel#photosLabel,
QLabel#linesLabel, QLabel#batteriesLabel, QLabel#coverageLabel {
    color: #F0A500; font-weight: bold;
}
QLabel#cameraInfoLabel { color: #7FB3E8; font-size: 10px; }
QWidget#actionBar {
    border-top: 1px solid #3A3D45; background-color: #181B22;
}
"""


class FlyPathDialog(QWidget):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface

        # State
        self._survey_polygon     = None
        self._survey_polygon_crs = None
        self._draw_tool          = None
        self._selected_layer_id  = None   # layer carrying the current map selection
        self._monitored_layer_id = None   # layer whose edit signals we're connected to
        self._prev_map_tool      = None
        self._survey_area_layer_id = None  # temporary drawn-polygon layer
        self._preview_layer_ids  = []     # [path_line_id, waypoints_id]
        self._waypoints          = []
        self._shot_spacing_m     = 0.0

        self._build_ui()
        self._setup_combos()
        self._connect_signals()
        self._update_camera_info()
        self._update_gsd()
        self._update_interval()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        arrow_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'arrow_down.svg'
        ).replace('\\', '/')
        self.setStyleSheet(STYLESHEET.replace('ARROW_DOWN_PATH', arrow_path))

        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        # Scrollable form area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        scroll_layout = QVBoxLayout(content)
        scroll_layout.setSpacing(8)
        scroll_layout.setContentsMargins(8, 8, 8, 8)

        scroll_layout.addWidget(self._build_mission_group())
        scroll_layout.addWidget(self._build_area_group())
        scroll_layout.addWidget(self._build_flight_group())
        scroll_layout.addWidget(self._build_camera_group())
        scroll_layout.addWidget(self._build_advanced_group())
        scroll_layout.addWidget(self._build_stats_group())
        scroll_layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        outer.addWidget(self._build_action_bar())

    def _build_mission_group(self):
        group = QGroupBox('Mission Setup')
        form  = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setSpacing(6)

        self.missionNameEdit = QLineEdit()
        self.missionNameEdit.setPlaceholderText('My Mission')
        form.addRow('Name', self.missionNameEdit)

        self.missionTypeCombo = QComboBox()
        form.addRow('Mission Type', self.missionTypeCombo)

        self.droneModelCombo = QComboBox()
        form.addRow('Drone', self.droneModelCombo)

        self.cameraInfoLabel = QLabel('—')
        self.cameraInfoLabel.setObjectName('cameraInfoLabel')
        form.addRow('Camera', self.cameraInfoLabel)

        return group

    def _build_area_group(self):
        group = QGroupBox('Survey Area')
        form  = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setSpacing(6)

        self.layerCombo = QComboBox()
        form.addRow('Layer', self.layerCombo)

        self.featureCombo = QComboBox()
        self.featureCombo.setVisible(False)
        self._featureComboRow = form.rowCount()
        form.addRow('Feature', self.featureCombo)

        draw_row = QWidget()
        draw_layout = QHBoxLayout(draw_row)
        draw_layout.setContentsMargins(0, 0, 0, 0)
        draw_layout.setSpacing(4)

        self.drawPolygonBtn = QPushButton('Draw Polygon on Map')
        self.drawPolygonBtn.setObjectName('drawPolygonBtn')
        self.drawPolygonBtn.setCheckable(True)

        self.removePolygonBtn = QPushButton('✕ Remove')
        self.removePolygonBtn.setObjectName('removePolygonBtn')
        self.removePolygonBtn.setToolTip('Remove drawn polygon and start over')
        self.removePolygonBtn.setVisible(False)

        draw_layout.addWidget(self.drawPolygonBtn)
        draw_layout.addWidget(self.removePolygonBtn)
        form.addRow(draw_row)

        self.areaLabel = QLabel('—')
        self.areaLabel.setObjectName('areaLabel')
        form.addRow('Area', self.areaLabel)

        return group

    def _build_flight_group(self):
        group = QGroupBox('Flight Parameters')
        form  = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setSpacing(6)

        self.altitudeSpin = QDoubleSpinBox()
        self.altitudeSpin.setRange(30.0, 500.0)
        self.altitudeSpin.setValue(100.0)
        self.altitudeSpin.setSingleStep(5.0)
        self.altitudeSpin.setDecimals(1)
        self.altitudeSpin.setSuffix(' m')
        form.addRow('Altitude', self.altitudeSpin)

        self.gsdLabel = QLabel('—')
        self.gsdLabel.setObjectName('gsdLabel')
        form.addRow('GSD', self.gsdLabel)

        self.frontOverlapSpin = QSpinBox()
        self.frontOverlapSpin.setRange(50, 95)
        self.frontOverlapSpin.setValue(80)
        self.frontOverlapSpin.setSuffix(' %')
        form.addRow('Front Overlap', self.frontOverlapSpin)

        self.sideOverlapSpin = QSpinBox()
        self.sideOverlapSpin.setRange(50, 95)
        self.sideOverlapSpin.setValue(70)
        self.sideOverlapSpin.setSuffix(' %')
        form.addRow('Side Overlap', self.sideOverlapSpin)

        self.speedSpin = QDoubleSpinBox()
        self.speedSpin.setRange(1.0, 15.0)
        self.speedSpin.setValue(5.0)
        self.speedSpin.setSingleStep(0.5)
        self.speedSpin.setDecimals(1)
        self.speedSpin.setSuffix(' m/s')
        form.addRow('Speed', self.speedSpin)

        # Direction row: spinbox + Auto button side by side
        dir_widget = QWidget()
        dir_layout = QHBoxLayout(dir_widget)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.setSpacing(4)

        self.directionSpin = QDoubleSpinBox()
        self.directionSpin.setRange(0.0, 359.9)
        self.directionSpin.setValue(0.0)
        self.directionSpin.setSingleStep(1.0)
        self.directionSpin.setDecimals(1)
        self.directionSpin.setSuffix(' °')
        self.directionSpin.setWrapping(True)

        self.autoDirectionBtn = QPushButton('Auto')
        self.autoDirectionBtn.setObjectName('autoDirectionBtn')
        self.autoDirectionBtn.setFixedWidth(52)

        dir_layout.addWidget(self.directionSpin)
        dir_layout.addWidget(self.autoDirectionBtn)
        form.addRow('Direction', dir_widget)

        self.marginSpin = QDoubleSpinBox()
        self.marginSpin.setRange(0.0, 200.0)
        self.marginSpin.setValue(0.0)
        self.marginSpin.setSingleStep(5.0)
        self.marginSpin.setDecimals(1)
        self.marginSpin.setSuffix(' m')
        form.addRow('Margin', self.marginSpin)

        return group

    def _build_camera_group(self):
        group = QGroupBox('Camera Settings')
        form  = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setSpacing(6)

        self.cameraAngleLabel = QLabel('90°  (Nadir — fixed for mapping)')
        form.addRow('Angle', self.cameraAngleLabel)

        self.triggerModeCombo = QComboBox()
        form.addRow('Trigger', self.triggerModeCombo)

        self.intervalLabel = QLabel('—')
        self.intervalLabel.setObjectName('intervalLabel')
        form.addRow('Interval', self.intervalLabel)

        return group

    def _build_advanced_group(self):
        group = QGroupBox('Advanced')
        form  = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setSpacing(6)

        self.altitudeModeCombo = QComboBox()
        form.addRow('Altitude Mode', self.altitudeModeCombo)

        self.startPointCombo = QComboBox()
        form.addRow('Start Point', self.startPointCombo)

        self.finishActionCombo = QComboBox()
        form.addRow('Finish Action', self.finishActionCombo)

        return group

    def _build_stats_group(self):
        group = QGroupBox('Statistics')
        form  = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setSpacing(6)

        stats = [
            ('flightTimeLabel', 'Flight Time'),
            ('distanceLabel',   'Distance'),
            ('photosLabel',     'Photos'),
            ('linesLabel',      'Flight Lines'),
            ('batteriesLabel',  'Batteries'),
            ('coverageLabel',   'Coverage'),
        ]
        for attr, caption in stats:
            lbl = QLabel('—')
            lbl.setObjectName(attr)
            setattr(self, attr, lbl)
            form.addRow(caption, lbl)

        return group

    def _build_action_bar(self):
        bar = QWidget()
        bar.setObjectName('actionBar')
        layout = QVBoxLayout(bar)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 6, 8, 10)

        self.previewBtn = QPushButton('Preview on Map')
        self.previewBtn.setMinimumHeight(30)

        self.clearPreviewBtn = QPushButton('Clear Preview')
        self.clearPreviewBtn.setObjectName('clearPreviewBtn')
        self.clearPreviewBtn.setMinimumHeight(26)

        self.exportBtn = QPushButton('Export KMZ')
        self.exportBtn.setObjectName('exportBtn')
        self.exportBtn.setMinimumHeight(36)

        layout.addWidget(self.previewBtn)
        layout.addWidget(self.clearPreviewBtn)
        layout.addWidget(self.exportBtn)

        return bar

    # ── Combo population ──────────────────────────────────────────────────

    def _setup_combos(self):
        self.missionTypeCombo.addItems([
            '2D Grid (Orthomosaic)',
        ])

        self.droneModelCombo.addItems(list(DRONE_SPECS.keys()))
        self.droneModelCombo.setCurrentText('DJI Mini 4 Pro')

        self.triggerModeCombo.addItems(['Distance (m)', 'Time (s)'])

        self.altitudeModeCombo.addItems([
            'AGL  (Relative to takeoff)',
            'MSL  (Absolute)',
        ])
        self.startPointCombo.addItems([
            'First waypoint',
            'Closest to takeoff point',
        ])
        self.finishActionCombo.addItems([
            'Return to Home',
            'Hover in place',
            'Land at last waypoint',
        ])
        self._refresh_layer_combo()

    def _refresh_layer_combo(self, _=None):
        previously_selected = self.layerCombo.currentData()
        self.layerCombo.blockSignals(True)
        self.layerCombo.clear()
        self.layerCombo.addItem('— none —', None)
        for layer in QgsProject.instance().mapLayers().values():
            if (hasattr(layer, 'wkbType') and
                    QgsWkbTypes.geometryType(layer.wkbType()) ==
                    QgsWkbTypes.PolygonGeometry and
                    not layer.customProperty('flypath_internal')):
                self.layerCombo.addItem(layer.name(), layer.id())
        # Restore previous selection if the layer still exists
        idx = self.layerCombo.findData(previously_selected)
        self.layerCombo.setCurrentIndex(idx if idx >= 0 else 0)
        self.layerCombo.blockSignals(False)

    # ── Signal wiring ─────────────────────────────────────────────────────

    def _connect_signals(self):
        # Refresh layer combo whenever layers are added or removed in the project
        QgsProject.instance().layersAdded.connect(self._refresh_layer_combo)
        QgsProject.instance().layersRemoved.connect(self._refresh_layer_combo)

        self.droneModelCombo.currentIndexChanged.connect(self._on_drone_changed)
        self.altitudeSpin.valueChanged.connect(self._on_param_changed)
        self.frontOverlapSpin.valueChanged.connect(self._on_param_changed)
        self.sideOverlapSpin.valueChanged.connect(self._on_param_changed)
        self.speedSpin.valueChanged.connect(self._on_param_changed)
        self.directionSpin.valueChanged.connect(self._update_stats)
        self.triggerModeCombo.currentIndexChanged.connect(self._update_interval)
        self.layerCombo.currentIndexChanged.connect(self._on_layer_changed)
        self.featureCombo.currentIndexChanged.connect(self._on_feature_changed)
        self.drawPolygonBtn.clicked.connect(self._on_draw_polygon)
        self.removePolygonBtn.clicked.connect(self._on_remove_drawn_polygon)
        self.autoDirectionBtn.clicked.connect(self._on_auto_direction)
        self.previewBtn.clicked.connect(self._on_preview)
        self.clearPreviewBtn.clicked.connect(self._on_clear_preview)
        self.exportBtn.clicked.connect(self._on_export)

    def _on_param_changed(self):
        self._update_gsd()
        self._update_interval()
        self._update_stats()
        self._on_clear_preview(reset_area=False)

    # ── Drone / camera ────────────────────────────────────────────────────

    def _on_drone_changed(self):
        self._update_camera_info()
        self._on_param_changed()

    def _update_camera_info(self):
        drone = self.droneModelCombo.currentText()
        self.cameraInfoLabel.setText(
            DRONE_SPECS[drone]['info'] if drone in DRONE_SPECS else '—'
        )

    # ── GSD ───────────────────────────────────────────────────────────────

    def _calc_gsd(self):
        drone = self.droneModelCombo.currentText()
        if drone not in DRONE_SPECS:
            return None
        s = DRONE_SPECS[drone]
        return round(
            (self.altitudeSpin.value() * s['sensor_width_mm'] * 100) /
            (s['focal_length_mm'] * s['image_width_px']), 2
        )

    def _update_gsd(self):
        gsd = self._calc_gsd()
        self.gsdLabel.setText(f'{gsd:.2f} cm/px' if gsd else '—')

    # ── Footprint & trigger interval ──────────────────────────────────────

    def _footprint(self):
        drone = self.droneModelCombo.currentText()
        if drone not in DRONE_SPECS:
            return None, None
        s   = DRONE_SPECS[drone]
        alt = self.altitudeSpin.value()
        return (alt * s['sensor_width_mm'] / s['focal_length_mm'],
                alt * s['sensor_height_mm'] / s['focal_length_mm'])

    def _update_interval(self):
        _, fh = self._footprint()
        if fh is None:
            self.intervalLabel.setText('—')
            return
        dist = fh * (1.0 - self.frontOverlapSpin.value() / 100.0)
        if self.triggerModeCombo.currentText().startswith('Distance'):
            self.intervalLabel.setText(f'{dist:.1f} m')
        else:
            spd = self.speedSpin.value()
            self.intervalLabel.setText(f'{dist / spd:.1f} s' if spd > 0 else '—')

    # ── Survey area ───────────────────────────────────────────────────────

    def _on_layer_changed(self):
        layer_id = self.layerCombo.currentData()
        self._disconnect_layer_signals()

        # Reset feature combo
        self.featureCombo.blockSignals(True)
        self.featureCombo.clear()
        self.featureCombo.setVisible(False)
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

        self._connect_layer_signals(layer)
        self._populate_feature_combo(layer)

    def _populate_feature_combo(self, layer):
        """Populate (or refresh) the feature combo for the given layer."""
        layer_id = layer.id()

        # Remember current selection to restore it after refresh
        prev_fid = self.featureCombo.currentData()

        self.featureCombo.blockSignals(True)
        self.featureCombo.clear()

        count = layer.featureCount()

        if count == 0:
            self.featureCombo.setVisible(False)
            self.featureCombo.blockSignals(False)
            self._survey_polygon     = None
            self._survey_polygon_crs = None
            self.areaLabel.setText('—')
            self._clear_stats()
            return

        if count == 1:
            self.featureCombo.setVisible(False)
            self.featureCombo.blockSignals(False)
            feat = next(layer.getFeatures())
            self._set_survey_polygon(feat.geometry(), layer.crs(),
                                     layer_id=layer_id, fid=feat.id())
        else:
            self.featureCombo.addItem('— select a feature —', None)
            name_field = self._guess_name_field(layer)
            for feat in layer.getFeatures():
                fid   = feat.id()
                label = (f'FID {fid}  —  {feat[name_field]}'
                         if name_field else f'FID {fid}')
                self.featureCombo.addItem(label, fid)
            self.featureCombo.setVisible(True)

            # Restore previous selection if the feature still exists
            idx = self.featureCombo.findData(prev_fid)
            if idx >= 0:
                self.featureCombo.setCurrentIndex(idx)
            else:
                # Previously selected feature was deleted — reset survey area
                self.featureCombo.setCurrentIndex(0)
                self._survey_polygon     = None
                self._survey_polygon_crs = None
                self.areaLabel.setText('—')
                self._clear_stats()

            self.featureCombo.blockSignals(False)

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
            except Exception:
                pass
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
        self._set_survey_polygon(feats[0].geometry(), layer.crs(),
                                 layer_id=layer_id, fid=fid)

    def _set_survey_polygon(self, geom, crs, layer_id=None, fid=None):
        self._on_clear_preview(reset_area=False)   # clear old flight path
        self._clear_layer_selection()
        self._survey_polygon     = geom
        self._survey_polygon_crs = crs
        # Highlight the chosen feature in the map canvas
        if layer_id and fid is not None:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer:
                layer.selectByIds([fid])
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
                'Missions this size will require multiple battery swaps. '
                'Check the estimated Batteries field in Statistics and '
                'plan your swap stops before flying.'
            )

    @staticmethod
    def _guess_name_field(layer):
        """Return the first text-like field name, or None."""
        from qgis.PyQt.QtCore import QVariant
        for field in layer.fields():
            if field.type() in (QVariant.String,):
                return field.name()
        return None

    def _on_draw_polygon(self, checked):
        if checked:
            if self._survey_polygon is not None:
                QMessageBox.warning(
                    self, 'Survey Area Already Defined',
                    'A survey polygon is already set.\n\n'
                    'Clear it first using the Clear Preview button before drawing a new one.'
                )
                self.drawPolygonBtn.setChecked(False)
                return
            canvas = self.iface.mapCanvas()
            self._prev_map_tool = canvas.mapTool()
            self._draw_tool = PolygonDrawTool(canvas)
            self._draw_tool.polygon_completed.connect(self._on_polygon_drawn)
            self._draw_tool.drawing_cancelled.connect(self._on_drawing_cancelled)
            canvas.setMapTool(self._draw_tool)
            self.drawPolygonBtn.setText('Drawing…  (right-click or double-click to finish)')
        else:
            self._cancel_draw_tool()

    def _on_polygon_drawn(self, geom):
        self.drawPolygonBtn.setChecked(False)
        self.drawPolygonBtn.setText('Draw Polygon on Map')
        if self._prev_map_tool:
            self.iface.mapCanvas().setMapTool(self._prev_map_tool)
            self._prev_map_tool = None
        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        self._show_drawn_polygon(geom, crs)
        self._set_survey_polygon(geom, crs)

    def _show_drawn_polygon(self, geom, crs):
        """Add the drawn survey boundary as a styled temporary layer."""
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
            'color': '45,109,181,50',
            'outline_color': '#2D6DB5',
            'outline_width': '0.8',
            'outline_style': 'dash',
        })
        layer.renderer().setSymbol(symbol)

        # Track geometry edits made via QGIS tools
        layer.editingStopped.connect(self._on_survey_area_edited)
        layer.geometryChanged.connect(self._on_survey_area_geometry_changed)

        QgsProject.instance().addMapLayer(layer)
        self._survey_area_layer_id = layer.id()
        self.removePolygonBtn.setVisible(True)
        self.iface.mapCanvas().refresh()

    def _on_survey_area_edited(self):
        """Called after the user commits edits to the survey area layer."""
        if not self._survey_area_layer_id:
            return
        layer = QgsProject.instance().mapLayer(self._survey_area_layer_id)
        if not layer or layer.featureCount() == 0:
            return
        feat = next(layer.getFeatures())
        self._survey_polygon     = feat.geometry()
        self._survey_polygon_crs = layer.crs()
        self._on_clear_preview(reset_area=False)
        area_ha = self._area_ha()
        self.areaLabel.setText(f'{area_ha:.2f} ha')
        self._update_stats()

    def _on_survey_area_geometry_changed(self, _fid, geom):
        """Update the stored polygon in real-time as the geometry is being edited."""
        if not self._survey_area_layer_id:
            return
        layer = QgsProject.instance().mapLayer(self._survey_area_layer_id)
        if not layer:
            return
        self._survey_polygon     = geom
        self._survey_polygon_crs = layer.crs()
        self._on_clear_preview(reset_area=False)
        self.areaLabel.setText(f'{self._area_ha():.2f} ha')
        self._update_stats()

    def _on_remove_drawn_polygon(self):
        """Remove the drawn polygon and reset the survey area."""
        self._remove_survey_area_layer()
        self._on_clear_preview(reset_area=False)
        self._survey_polygon     = None
        self._survey_polygon_crs = None
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
                except Exception:
                    pass
                QgsProject.instance().removeMapLayer(self._survey_area_layer_id)
            self._survey_area_layer_id = None
        self.removePolygonBtn.setVisible(False)

    def _on_drawing_cancelled(self):
        self.drawPolygonBtn.setChecked(False)
        self.drawPolygonBtn.setText('Draw Polygon on Map')
        if self._prev_map_tool:
            self.iface.mapCanvas().setMapTool(self._prev_map_tool)
            self._prev_map_tool = None

    def _cancel_draw_tool(self):
        if self._draw_tool:
            self.iface.mapCanvas().unsetMapTool(self._draw_tool)
            self._draw_tool = None

    def _area_ha(self):
        """Return survey polygon area in hectares (metric, via EPSG:3857)."""
        if self._survey_polygon is None:
            return 0.0
        utm = QgsCoordinateReferenceSystem('EPSG:3857')
        xf  = QgsCoordinateTransform(
            self._survey_polygon_crs, utm, QgsProject.instance()
        )
        g = QgsGeometry(self._survey_polygon)
        g.transform(xf)
        return g.area() / 10_000

    # ── Auto direction ────────────────────────────────────────────────────

    def _on_auto_direction(self):
        if not self._has_survey_area(silent=True):
            QMessageBox.information(
                self, 'No Survey Area',
                'Define a survey area first to enable automatic direction optimisation.'
            )
            return
        fw, _ = self._footprint()
        if fw is None:
            return
        line_spacing = fw * (1.0 - self.sideOverlapSpin.value() / 100.0)
        best = find_optimal_direction(
            self._survey_polygon, self._survey_polygon_crs, line_spacing
        )
        self.directionSpin.setValue(best)

    # ── Statistics ────────────────────────────────────────────────────────

    def _update_stats(self):
        if not self._has_survey_area(silent=True):
            self._clear_stats()
            return
        drone = self.droneModelCombo.currentText()
        if drone not in DRONE_SPECS:
            self._clear_stats()
            return

        s     = DRONE_SPECS[drone]
        speed = self.speedSpin.value()
        fw, fh = self._footprint()
        if fw is None:
            self._clear_stats()
            return

        line_spacing = max(fw * (1.0 - self.sideOverlapSpin.value()  / 100.0), 0.5)
        shot_spacing = max(fh * (1.0 - self.frontOverlapSpin.value() / 100.0), 0.5)

        utm = QgsCoordinateReferenceSystem('EPSG:3857')
        xf  = QgsCoordinateTransform(
            self._survey_polygon_crs, utm, QgsProject.instance()
        )
        g = QgsGeometry(self._survey_polygon)
        g.transform(xf)
        bbox  = g.boundingBox()
        a_rad = math.radians(self.directionSpin.value())

        across = (abs(bbox.width()  * math.cos(a_rad)) +
                  abs(bbox.height() * math.sin(a_rad)))
        along  = (abs(bbox.width()  * math.sin(a_rad)) +
                  abs(bbox.height() * math.cos(a_rad)))

        n_lines    = max(1, math.ceil(across / line_spacing) + 1)
        dist_m     = n_lines * along + (n_lines - 1) * line_spacing
        n_photos   = max(0, int(dist_m / shot_spacing))
        flight_min = dist_m / (speed * 60) if speed > 0 else 0
        batteries  = math.ceil(flight_min / s['battery_time_min'])
        area_ha    = g.area() / 10_000

        self.flightTimeLabel.setText(f'{flight_min:.1f} min')
        self.distanceLabel.setText(f'{dist_m / 1000:.2f} km')
        self.photosLabel.setText(f'{n_photos:,}')
        self.linesLabel.setText(str(n_lines))
        self.batteriesLabel.setText(str(batteries))
        self.coverageLabel.setText(f'{area_ha:.2f} ha')

    def _clear_stats(self):
        for attr in ('flightTimeLabel', 'distanceLabel', 'photosLabel',
                     'linesLabel', 'batteriesLabel', 'coverageLabel'):
            getattr(self, attr).setText('—')
        self.areaLabel.setText('—')
        self.gsdLabel.setText('—')
        self.intervalLabel.setText('—')

    # ── Map preview ───────────────────────────────────────────────────────

    def _on_preview(self):
        if not self._has_survey_area():
            return
        result = self._generate_waypoints()
        if not result:
            QMessageBox.warning(
                self, 'No Waypoints',
                'The grid produced no waypoints.\n'
                'Try increasing the survey area or reducing overlap values.'
            )
            return
        waypoints, shot_spacing_m = result
        self._waypoints      = waypoints
        self._shot_spacing_m = shot_spacing_m
        self._on_clear_preview(reset_area=False)

        # ── 1. Flight path line ───────────────────────────────────────────
        line_layer = QgsVectorLayer(
            'LineString?crs=EPSG:4326&field=id:integer',
            'FlyPath — Path', 'memory'
        )
        line_layer.setCustomProperty('flypath_internal', True)
        dp   = line_layer.dataProvider()
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(lon, lat) for lon, lat in waypoints]
        ))
        feat.setAttributes([0])
        dp.addFeatures([feat])

        line_sym = QgsLineSymbol.createSimple({
            'color': '#2D6DB5', 'width': '0.7',
            'capstyle': 'round', 'joinstyle': 'round',
        })
        line_layer.renderer().setSymbol(line_sym)
        QgsProject.instance().addMapLayer(line_layer)

        # ── 2. Waypoint markers ───────────────────────────────────────────
        wp_layer = QgsVectorLayer(
            'Point?crs=EPSG:4326&field=seq:integer&field=wp_type:string(10)',
            'FlyPath — Waypoints', 'memory'
        )
        wp_layer.setCustomProperty('flypath_internal', True)
        dp2      = wp_layer.dataProvider()
        last_idx = len(waypoints) - 1
        wp_feats = []
        for i, (lon, lat) in enumerate(waypoints):
            f = QgsFeature()
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
            if i == 0:
                wp_type = 'start'
            elif i == last_idx:
                wp_type = 'end'
            else:
                wp_type = 'mid'
            f.setAttributes([i + 1, wp_type])
            wp_feats.append(f)
        dp2.addFeatures(wp_feats)

        # Rule-based renderer: start=green, end=amber, mid=white/blue
        root = QgsRuleBasedRenderer.Rule(None)
        for expr, color, border, size, label in [
            ('"wp_type" = \'start\'', '#40C040', 'white',   '4.0', 'Start'),
            ('"wp_type" = \'end\'',   '#F0A500', 'white',   '4.0', 'End'),
            ('"wp_type" = \'mid\'',   'white',   '#2D6DB5', '2.8', 'Waypoint'),
        ]:
            sym = QgsMarkerSymbol.createSimple({
                'name': 'circle', 'color': color,
                'outline_color': border, 'outline_width': '0.4',
                'size': size,
            })
            rule = QgsRuleBasedRenderer.Rule(sym, filterExp=expr, label=label)
            root.appendChild(rule)
        wp_layer.setRenderer(QgsRuleBasedRenderer(root))

        # Labels: sequence number centred on each marker
        lbl = QgsPalLayerSettings()
        lbl.fieldName  = 'seq'
        lbl.placement  = QgsPalLayerSettings.OverPoint
        lbl.priority   = 10
        fmt = QgsTextFormat()
        fmt.setFont(QFont('Segoe UI', 7, QFont.Bold))
        fmt.setColor(QColor('#1E2128'))
        fmt.setSize(7)
        lbl.setFormat(fmt)
        wp_layer.setLabeling(QgsVectorLayerSimpleLabeling(lbl))
        wp_layer.setLabelsEnabled(True)

        QgsProject.instance().addMapLayer(wp_layer)
        self._preview_layer_ids = [line_layer.id(), wp_layer.id()]
        self.iface.mapCanvas().refresh()

    def _on_clear_preview(self, reset_area=True):
        # Always remove the flight-path preview layers
        for lid in self._preview_layer_ids:
            if QgsProject.instance().mapLayer(lid):
                QgsProject.instance().removeMapLayer(lid)
        self._preview_layer_ids = []

        if reset_area:
            # Full reset — also remove the survey boundary layer
            self._remove_survey_area_layer()
            self._clear_layer_selection()
            self._survey_polygon     = None
            self._survey_polygon_crs = None
            self._waypoints          = []
            self._shot_spacing_m     = 0.0
            self.areaLabel.setText('—')
            self.layerCombo.setCurrentIndex(0)
            self.featureCombo.clear()
            self.featureCombo.setVisible(False)
            self._clear_stats()

        self.iface.mapCanvas().refresh()

    # ── Export ────────────────────────────────────────────────────────────

    def _on_export(self):
        if not self._has_survey_area():
            return
        mission = self.missionNameEdit.text().strip() or 'FlyPath Mission'
        filepath, _ = QFileDialog.getSaveFileName(
            self, 'Save KMZ Mission File',
            mission.replace(' ', '_') + '.kmz',
            'DJI Mission File (*.kmz)'
        )
        if not filepath:
            return

        if self._waypoints and self._shot_spacing_m:
            waypoints      = self._waypoints
            shot_spacing_m = self._shot_spacing_m
        else:
            result = self._generate_waypoints()
            if not result:
                QMessageBox.warning(self, 'No Waypoints',
                                    'The grid produced no waypoints.')
                return
            waypoints, shot_spacing_m = result

        try:
            write_kmz(
                filepath=filepath,
                waypoints=waypoints,
                drone_name=self.droneModelCombo.currentText(),
                altitude_m=self.altitudeSpin.value(),
                speed_ms=self.speedSpin.value(),
                finish_action_label=self.finishActionCombo.currentText(),
                altitude_mode_label=self.altitudeModeCombo.currentText(),
                shot_spacing_m=shot_spacing_m,
                mission_name=mission,
            )
            QMessageBox.information(
                self, 'Export Complete',
                f'Saved to:\n{filepath}\n\n'
                f'Turn waypoints: {len(waypoints):,}\n'
                f'Photo interval: {shot_spacing_m:.1f} m\n\n'
                'Load the .kmz in the DJI Fly app to fly the mission.'
            )
        except Exception as exc:
            QMessageBox.critical(self, 'Export Failed', str(exc))

    # ── Helpers ───────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._disconnect_layer_signals()
        self._remove_survey_area_layer()
        self._on_clear_preview(reset_area=False)
        try:
            QgsProject.instance().layersAdded.disconnect(self._refresh_layer_combo)
            QgsProject.instance().layersRemoved.disconnect(self._refresh_layer_combo)
        except Exception:
            pass
        super().closeEvent(event)

    def _has_survey_area(self, silent=False):
        if self._survey_polygon is None or self._survey_polygon_crs is None:
            if not silent:
                QMessageBox.information(
                    self, 'No Survey Area',
                    'Draw a polygon on the map or select a polygon layer first.'
                )
            return False
        return True

    def _generate_waypoints(self):
        """
        Returns (waypoints, shot_spacing_m) or None on failure.
        waypoints is a list of (lon, lat) turn points only.
        """
        drone = self.droneModelCombo.currentText()
        if drone not in DRONE_SPECS:
            return None
        waypoints, shot_spacing_m = generate_flight_grid(
            polygon_geom=self._survey_polygon,
            polygon_crs=self._survey_polygon_crs,
            altitude_m=self.altitudeSpin.value(),
            front_overlap=self.frontOverlapSpin.value() / 100.0,
            side_overlap=self.sideOverlapSpin.value() / 100.0,
            direction_deg=self.directionSpin.value(),
            margin_m=self.marginSpin.value(),
            drone_specs=DRONE_SPECS[drone],
        )
        if not waypoints:
            return None
        return waypoints, shot_spacing_m
