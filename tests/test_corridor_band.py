"""The corridor band follows its centre line, as on flypath.io (no network)."""

import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_corridor_band_shows_with_the_line_and_follows_the_buffer():
    try:
        from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsProject
        from qgis.gui import QgsMapCanvas
        from qgis.PyQt.QtCore import QSettings
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    dialog = importlib.import_module(Path(__file__).resolve().parents[1].name + '.flypath_dialog')
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    ini = os.path.join(tempfile.mkdtemp(), 'settings.ini')
    ini_format = getattr(QSettings, 'Format', QSettings).IniFormat
    canvas = QgsMapCanvas()
    line = QgsGeometry.fromWkt('LINESTRING(-114.09 51.05, -114.08 51.052)')
    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')

    def band_area(planner):
        layer_id = planner._corridor_band_layer_id
        layer = QgsProject.instance().mapLayer(layer_id) if layer_id else None
        return next(layer.getFeatures()).geometry().area() if layer else None

    def draw(planner):
        planner._show_drawn_line(line, wgs84)
        planner._set_survey_line(line, wgs84)
        app.processEvents()

    with patch.object(dialog, 'QSettings', lambda *args: QSettings(ini, ini_format)):
        planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            planner.show()
            planner.missionTypeCombo.setCurrentText('Corridor Mapping')
            assert band_area(planner) is None
            draw(planner)
            narrow = band_area(planner)
            assert narrow, 'the band shows as soon as the line is drawn, before Preview'
            planner.bufferSpin.setValue(planner.bufferSpin.value() * 2)
            app.processEvents()
            assert band_area(planner) > narrow * 1.5, 'the band follows the Buffer'
            planner._on_preview()
            assert band_area(planner), 'the band stays with the preview'
            planner._on_remove_drawn_polygon()
            assert band_area(planner) is None, 'removing the line removes the band'
            draw(planner)
            planner.missionTypeCombo.setCurrentText('2D Mapping')
            assert band_area(planner) is None, '2D Mapping has no band'
            planner.missionTypeCombo.setCurrentText('Corridor Mapping')
            draw(planner)
            band_id = planner._corridor_band_layer_id
        finally:
            planner.close()
            app.processEvents()
        assert QgsProject.instance().mapLayer(band_id) is None, 'a closed panel leaves no band'
    canvas.close()
