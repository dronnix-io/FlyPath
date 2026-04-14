from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QColor
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.core import QgsWkbTypes, QgsGeometry, QgsPointXY


class PolygonDrawTool(QgsMapTool):
    """
    Interactive polygon drawing tool that mimics QGIS's native digitising UX.

    Behaviour
    ---------
    Left-click        : place a vertex
    Move mouse        : rubber band follows cursor, polygon always closes back
                        to the first vertex so you see the full shape at all times
    Right-click       : finish (minimum 3 vertices required)
    Double-click      : finish using the point placed by the preceding click
    Backspace / Delete: undo the last vertex
    Escape            : cancel and emit drawing_cancelled

    Snapping
    --------
    Respects the project's snapping configuration via the canvas snapping utils.
    """

    polygon_completed = pyqtSignal(object)   # QgsGeometry (Polygon)
    drawing_cancelled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._points  = []
        self._markers = []          # QgsVertexMarker for each placed vertex
        self._cursor  = None        # last known cursor position (map coords)

        # Single polygon rubber band — Qt auto-closes it back to the first point
        self._band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self._band.setColor(QColor(240, 165, 0, 50))        # orange semi-fill
        self._band.setStrokeColor(QColor(240, 165, 0, 220))
        self._band.setWidth(2)
        self._band.setLineStyle(Qt.DashLine)

    # ── Snapping ──────────────────────────────────────────────────────────

    def _snap(self, pos):
        """Return the snapped map point for a canvas pixel position."""
        try:
            match = self.canvas().snappingUtils().snapToMap(pos)
            if match.isValid():
                return match.point()
        except Exception:
            pass
        return self.toMapCoordinates(pos)

    # ── Rubber-band update ────────────────────────────────────────────────

    def _redraw(self, cursor_pt=None):
        """Rebuild the rubber band from placed points + optional cursor position."""
        self._band.reset(QgsWkbTypes.PolygonGeometry)
        pts = self._points + ([cursor_pt] if cursor_pt else [])
        for i, pt in enumerate(pts):
            self._band.addPoint(pt, i == len(pts) - 1)

    # ── Mouse events ──────────────────────────────────────────────────────

    def canvasMoveEvent(self, event):
        if not self._points:
            return
        self._cursor = self._snap(event.pos())
        self._redraw(self._cursor)

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pt = self._snap(event.pos())
            self._points.append(pt)
            self._add_marker(pt)
            self._redraw(self._cursor)
        elif event.button() == Qt.RightButton:
            self._finish()

    def canvasDoubleClickEvent(self, event):
        # canvasPressEvent already fired and placed the vertex for this
        # double-click — just finish without adding a duplicate.
        self._finish()

    # ── Keyboard events ───────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            self._reset()
            self.drawing_cancelled.emit()
        elif key in (Qt.Key_Backspace, Qt.Key_Delete):
            self._undo_last()

    # ── Vertex markers ────────────────────────────────────────────────────

    def _add_marker(self, pt):
        m = QgsVertexMarker(self.canvas())
        m.setCenter(pt)
        m.setIconType(QgsVertexMarker.ICON_BOX)
        m.setColor(QColor(45, 109, 181))
        m.setFillColor(QColor(255, 255, 255, 200))
        m.setIconSize(8)
        m.setPenWidth(2)
        self._markers.append(m)

    def _remove_markers(self):
        for m in self._markers:
            self.canvas().scene().removeItem(m)
        self._markers.clear()

    # ── Internal ──────────────────────────────────────────────────────────

    def _undo_last(self):
        if not self._points:
            return
        self._points.pop()
        if self._markers:
            self.canvas().scene().removeItem(self._markers.pop())
        self._redraw(self._cursor)

    def _finish(self):
        if len(self._points) >= 3:
            geom = QgsGeometry.fromPolygonXY([list(self._points)])
            self._reset()
            self.polygon_completed.emit(geom)
        else:
            self._reset()

    def _reset(self):
        self._points.clear()
        self._cursor = None
        self._band.reset(QgsWkbTypes.PolygonGeometry)
        self._remove_markers()

    def deactivate(self):
        self._reset()
        super().deactivate()
