"""Drawing completion must not introduce zero-length survey edges."""

import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_finishing_polygon_and_line_preserves_distinct_vertices():
    try:
        from qgis.core import QgsApplication, QgsPointXY
        from qgis.gui import QgsMapCanvas
        from FlyPath import map_tools
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    canvas = QgsMapCanvas()
    try:
        for tool_class in (map_tools.PolygonDrawTool, map_tools.LineDrawTool):
            for finish in ('same_point', 'new_point', 'double_click'):
                tool = tool_class(canvas)
                tool._snap = lambda point: point
                completed = []
                signal = (tool.polygon_completed if tool_class is map_tools.PolygonDrawTool
                          else tool.line_completed)
                signal.connect(completed.append)
                points = [QgsPointXY(0, 0), QgsPointXY(1, 0),
                          QgsPointXY(1, 1), QgsPointXY(0, 1)]

                def press(point, button, draw_tool=tool):
                    draw_tool.canvasPressEvent(SimpleNamespace(
                        pos=lambda: point, button=lambda: button))

                for point in points[:-1] if finish == 'new_point' else points:
                    press(point, map_tools._LeftButton)
                if finish == 'double_click':
                    press(points[-1], map_tools._LeftButton)
                    tool.canvasDoubleClickEvent(None)
                else:
                    press(points[-1], map_tools._RightButton)
                assert len(completed) == 1
                polygon = tool_class is map_tools.PolygonDrawTool
                vertices = (completed[0].asPolygon()[0] if polygon
                            else completed[0].asPolyline())
                assert vertices == points + ([points[0]] if polygon else []), finish
                assert not tool._points
                tool.deactivate()
                del tool
    finally:
        canvas.close()
        app.processEvents()
