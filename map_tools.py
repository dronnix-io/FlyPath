from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QColor
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.core import QgsWkbTypes, QgsGeometry

try:
    _DashLine     = Qt.PenStyle.DashLine
    _LeftButton   = Qt.MouseButton.LeftButton
    _RightButton  = Qt.MouseButton.RightButton
    _Key_Escape   = Qt.Key.Key_Escape
    _Key_Backspace = Qt.Key.Key_Backspace
    _Key_Delete   = Qt.Key.Key_Delete
except AttributeError:
    # Old PyQt5 without scoped enums; fetch the unscoped names dynamically.
    _DashLine     = getattr(Qt, 'DashLine')
    _LeftButton   = getattr(Qt, 'LeftButton')
    _RightButton  = getattr(Qt, 'RightButton')
    _Key_Escape   = getattr(Qt, 'Key_Escape')
    _Key_Backspace = getattr(Qt, 'Key_Backspace')
    _Key_Delete   = getattr(Qt, 'Key_Delete')

try:
    _PolygonGeometry = QgsWkbTypes.GeometryType.PolygonGeometry
    _IconBox         = QgsVertexMarker.IconType.ICON_BOX
except AttributeError:
    _PolygonGeometry = getattr(QgsWkbTypes, 'PolygonGeometry')
    _IconBox         = getattr(QgsVertexMarker, 'ICON_BOX')


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
        self._band = QgsRubberBand(canvas, _PolygonGeometry)
        self._band.setColor(QColor(255, 20, 147, 80))         # deep pink semi-fill
        self._band.setStrokeColor(QColor(255, 20, 147, 220))
        self._band.setWidth(2)
        self._band.setLineStyle(_DashLine)

    # ── Snapping ──────────────────────────────────────────────────────────

    def _snap(self, pos):
        """Return the snapped map point for a canvas pixel position."""
        try:
            match = self.canvas().snappingUtils().snapToMap(pos)
            if match.isValid():
                return match.point()
        except (RuntimeError, AttributeError):
            pass
        return self.toMapCoordinates(pos)

    # ── Rubber-band update ────────────────────────────────────────────────

    def _redraw(self, cursor_pt=None):
        """Rebuild the rubber band from placed points + optional cursor position."""
        self._band.reset(_PolygonGeometry)
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
        if event.button() == _LeftButton:
            pt = self._snap(event.pos())
            self._points.append(pt)
            self._add_marker(pt)
            self._redraw(self._cursor)
        elif event.button() == _RightButton:
            pt = self._snap(event.pos())
            self._points.append(pt)
            self._add_marker(pt)
            self._finish()

    def canvasDoubleClickEvent(self, event):
        # canvasPressEvent already fired and placed the vertex for this
        # double-click — just finish without adding a duplicate.
        self._finish()

    # ── Keyboard events ───────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        if key == _Key_Escape:
            self._reset()
            self.drawing_cancelled.emit()
        elif key in (_Key_Backspace, _Key_Delete):
            self._undo_last()

    # ── Vertex markers ────────────────────────────────────────────────────

    def _add_marker(self, pt):
        m = QgsVertexMarker(self.canvas())
        m.setCenter(pt)
        m.setIconType(_IconBox)
        m.setColor(QColor(255, 20, 147))
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
        self._band.reset(_PolygonGeometry)
        self._remove_markers()

    def deactivate(self):
        self._reset()
        super().deactivate()
