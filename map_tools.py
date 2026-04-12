from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QColor
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.core import QgsWkbTypes, QgsGeometry, QgsPointXY


class PolygonDrawTool(QgsMapTool):
    """
    Interactive rubber-band polygon drawing tool for the QGIS map canvas.

    Usage:
        tool = PolygonDrawTool(iface.mapCanvas())
        tool.polygon_completed.connect(my_slot)
        iface.mapCanvas().setMapTool(tool)

    Left-click  : add vertex
    Right-click : finish polygon
    Double-click: finish polygon
    Escape      : cancel and emit drawing_cancelled
    """

    polygon_completed = pyqtSignal(object)   # emits QgsGeometry (Polygon)
    drawing_cancelled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._points = []

        # Filled polygon preview
        self._fill_band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self._fill_band.setColor(QColor(80, 200, 80, 45))
        self._fill_band.setStrokeColor(QColor(80, 200, 80, 220))
        self._fill_band.setWidth(2)

        # Cursor line from last vertex to current mouse position
        self._line_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._line_band.setColor(QColor(80, 200, 80, 180))
        self._line_band.setWidth(1)

    # ── Mouse events ──────────────────────────────────────────────────────

    def canvasMoveEvent(self, event):
        if not self._points:
            return
        pt = self.toMapCoordinates(event.pos())
        self._line_band.reset(QgsWkbTypes.LineGeometry)
        self._line_band.addPoint(self._points[-1])
        self._line_band.addPoint(pt, True)

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pt = self.toMapCoordinates(event.pos())
            self._points.append(QgsPointXY(pt))
            self._fill_band.addPoint(pt, True)
        elif event.button() == Qt.RightButton:
            self._finish()

    def canvasDoubleClickEvent(self, event):
        # The single-click for the double-click has already added a point;
        # remove the duplicate, then finish.
        if self._points:
            self._points.pop()
        self._finish()

    # ── Keyboard events ───────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._reset()
            self.drawing_cancelled.emit()
        elif event.key() == Qt.Key_Backspace and self._points:
            self._points.pop()
            self._fill_band.removeLastPoint()

    # ── Internal ──────────────────────────────────────────────────────────

    def _finish(self):
        if len(self._points) >= 3:
            geom = QgsGeometry.fromPolygonXY([list(self._points)])
            self._reset()
            self.polygon_completed.emit(geom)
        else:
            self._reset()

    def _reset(self):
        self._points.clear()
        self._fill_band.reset(QgsWkbTypes.PolygonGeometry)
        self._line_band.reset(QgsWkbTypes.LineGeometry)

    def deactivate(self):
        self._reset()
        super().deactivate()
