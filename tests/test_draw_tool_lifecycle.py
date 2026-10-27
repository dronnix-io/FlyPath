"""The draw tool is only active while Draw is pressed (no network)."""

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


def test_draw_tool_stops_with_the_draw_button_and_the_panel():
    try:
        from qgis.core import QgsApplication
        from qgis.gui import QgsMapCanvas, QgsMapToolPan
        from qgis.PyQt.QtCore import QSettings
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    package = Path(__file__).resolve().parents[1].name
    dialog = importlib.import_module(package + '.flypath_dialog')
    map_tools = importlib.import_module(package + '.map_tools')
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    ini = os.path.join(tempfile.mkdtemp(), 'settings.ini')
    ini_format = getattr(QSettings, 'Format', QSettings).IniFormat
    canvas = QgsMapCanvas()
    pan = QgsMapToolPan(canvas)

    def drawing():
        return isinstance(canvas.mapTool(), (map_tools.PolygonDrawTool, map_tools.LineDrawTool))

    def check(name, kind, steps):
        canvas.setMapTool(pan)
        planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            planner.show()
            planner.missionTypeCombo.setCurrentText(kind)
            assert not drawing(), 'no drawing before Draw is pressed'
            planner.drawPolygonBtn.click()
            assert drawing() and planner.drawPolygonBtn.isChecked(), name
            steps(planner)
            app.processEvents()
            assert not drawing(), name + ': the draw tool must stop'
            assert not planner.drawPolygonBtn.isChecked(), name + ': Draw must be released'
            assert planner.drawPolygonBtn.text() == planner._draw_btn_default_text(), name
        finally:
            planner.close()
            app.processEvents()

    with patch.object(dialog, 'QSettings', lambda *args: QSettings(ini, ini_format)):
        for kind in ('2D Mapping', 'Corridor Mapping', 'Oblique Mapping'):
            check('source to layer', kind, lambda p: p.sourceLayerRadio.click())
            check('source to selection', kind, lambda p: p.sourceSelectionRadio.click())
            check('Draw pressed again', kind, lambda p: p.drawPolygonBtn.click())
            check('area cleared', kind, lambda p: p._on_clear_preview(reset_area=True))
        check('type changed', '2D Mapping', lambda p: p.missionTypeCombo.setCurrentText('Corridor Mapping'))
        check('type changed to orbit', 'Oblique Mapping',
              lambda p: p.missionTypeCombo.setCurrentText('Orbit (3D Model)'))

        # Closing the panel, as when the plugin is reloaded, stops drawing too.
        for kind in ('2D Mapping', 'Corridor Mapping'):
            canvas.setMapTool(pan)
            planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
            planner.show()
            planner.missionTypeCombo.setCurrentText(kind)
            planner.drawPolygonBtn.click()
            assert drawing()
            planner.close()
            app.processEvents()
            assert not drawing(), kind + ': a closed panel must not keep drawing'
    # A drawing tool left on the map by an earlier copy of the plugin (closed,
    # or reloaded while drawing) is not the class loaded now and carries no
    # name. The panel must turn it off, and drawing must never bring it back.
    import types
    old_copy = types.ModuleType('OldFlyPath.map_tools')
    exec('from qgis.gui import QgsMapTool\n'
         'class PolygonDrawTool(QgsMapTool):\n'
         '    clicks = 0\n'
         '    def canvasPressEvent(self, event):\n'
         '        PolygonDrawTool.clicks += 1\n', old_copy.__dict__)
    with patch.object(dialog, 'QSettings', lambda *args: QSettings(ini, ini_format)):
        leftover = old_copy.PolygonDrawTool(canvas)
        canvas.setMapTool(leftover)
        assert map_tools.is_flypath_tool(leftover)
        planner = dialog.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
        try:
            planner.show()
            assert canvas.mapTool() is not leftover, 'a leftover FlyPath tool must not keep drawing'
            canvas.setMapTool(leftover)              # it comes back before Draw is pressed
            planner.drawPolygonBtn.click()
            planner.drawPolygonBtn.click()           # Draw off again
            app.processEvents()
            assert canvas.mapTool() is not leftover, 'Draw must not restore a leftover tool'
            assert not map_tools.is_flypath_tool(canvas.mapTool())
        finally:
            planner.close()
            app.processEvents()
    assert not map_tools.is_flypath_tool(canvas.mapTool())

    # Release the test's own map tool before its canvas, so Qt never deletes
    # the tool after the canvas it points to.
    canvas.unsetMapTool(pan)
    canvas.close()
    canvas.deleteLater()
    app.processEvents()
