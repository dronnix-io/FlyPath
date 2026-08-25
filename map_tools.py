import math

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
    _LineGeometry    = QgsWkbTypes.GeometryType.LineGeometry
    _IconBox         = QgsVertexMarker.IconType.ICON_BOX
    _IconCircle      = QgsVertexMarker.IconType.ICON_CIRCLE
except AttributeError:
    _PolygonGeometry = getattr(QgsWkbTypes, 'PolygonGeometry')
    _LineGeometry    = getattr(QgsWkbTypes, 'LineGeometry')
    _IconBox         = getattr(QgsVertexMarker, 'ICON_BOX')
    _IconCircle      = getattr(QgsVertexMarker, 'ICON_CIRCLE')


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


class LineDrawTool(QgsMapTool):
    """
    Interactive line (polyline) drawing tool for corridor centre lines.

    Shares the digitising UX of PolygonDrawTool but produces an open LineString
    and needs a minimum of two vertices.

    Left-click        : place a vertex
    Move mouse        : rubber band follows the cursor
    Right-click       : finish (minimum 2 vertices required)
    Double-click      : finish using the point placed by the preceding click
    Backspace / Delete: undo the last vertex
    Escape            : cancel and emit drawing_cancelled
    """

    line_completed    = pyqtSignal(object)   # QgsGeometry (LineString)
    drawing_cancelled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._points  = []
        self._markers = []
        self._cursor  = None

        self._band = QgsRubberBand(canvas, _LineGeometry)
        self._band.setColor(QColor(255, 20, 147, 220))        # deep pink line
        self._band.setWidth(2)
        self._band.setLineStyle(_DashLine)

    def _snap(self, pos):
        try:
            match = self.canvas().snappingUtils().snapToMap(pos)
            if match.isValid():
                return match.point()
        except (RuntimeError, AttributeError):
            pass
        return self.toMapCoordinates(pos)

    def _redraw(self, cursor_pt=None):
        self._band.reset(_LineGeometry)
        pts = self._points + ([cursor_pt] if cursor_pt else [])
        for i, pt in enumerate(pts):
            self._band.addPoint(pt, i == len(pts) - 1)

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
        self._finish()

    def keyPressEvent(self, event):
        key = event.key()
        if key == _Key_Escape:
            self._reset()
            self.drawing_cancelled.emit()
        elif key in (_Key_Backspace, _Key_Delete):
            self._undo_last()

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

    def _undo_last(self):
        if not self._points:
            return
        self._points.pop()
        if self._markers:
            self.canvas().scene().removeItem(self._markers.pop())
        self._redraw(self._cursor)

    def _finish(self):
        if len(self._points) >= 2:
            geom = QgsGeometry.fromPolylineXY(list(self._points))
            self._reset()
            self.line_completed.emit(geom)
        else:
            self._reset()

    def _reset(self):
        self._points.clear()
        self._cursor = None
        self._band.reset(_LineGeometry)
        self._remove_markers()

    def deactivate(self):
        self._reset()
        super().deactivate()


class VertexPickTool(QgsMapTool):
    """
    Vertex-snapping pick tool. Given a set of target points (map coordinates),
    it highlights the nearest one as the cursor approaches and snaps the click to
    it, emitting that point. FlyPath uses it to let the user click corridor
    centre-line vertices to toggle mission breaks. Emits `finished` on Escape.
    """

    point_picked = pyqtSignal(object)   # QgsPointXY in map coordinates (snapped)
    finished     = pyqtSignal()

    _SNAP_PX = 15                       # snap radius in screen pixels

    def __init__(self, canvas):
        super().__init__(canvas)
        self._targets = []             # candidate QgsPointXY in map coordinates
        self._marker = None

    def set_targets(self, points):
        """Set the points to snap to (QgsPointXY in the canvas map CRS)."""
        self._targets = list(points)

    def _nearest(self, pos):
        """Nearest target within the snap radius of pixel position `pos`, or None."""
        best, best_d = None, float(self._SNAP_PX)
        for pt in self._targets:
            cp = self.toCanvasCoordinates(pt)
            d = math.hypot(cp.x() - pos.x(), cp.y() - pos.y())
            if d <= best_d:
                best, best_d = pt, d
        return best

    def canvasMoveEvent(self, event):
        self._show_marker(self._nearest(event.pos()))

    def canvasPressEvent(self, event):
        if event.button() == _LeftButton:
            snapped = self._nearest(event.pos())
            self.point_picked.emit(
                snapped if snapped is not None
                else self.toMapCoordinates(event.pos()))

    def keyPressEvent(self, event):
        if event.key() == _Key_Escape:
            self.finished.emit()

    def _show_marker(self, pt):
        if pt is None:
            self._remove_marker()
            return
        if self._marker is None:
            self._marker = QgsVertexMarker(self.canvas())
            self._marker.setIconType(_IconCircle)
            self._marker.setColor(QColor(255, 212, 0))
            self._marker.setFillColor(QColor(255, 212, 0, 90))
            self._marker.setIconSize(16)
            self._marker.setPenWidth(3)
        self._marker.setCenter(pt)

    def _remove_marker(self):
        if self._marker is not None:
            self.canvas().scene().removeItem(self._marker)
            self._marker = None

    def deactivate(self):
        self._remove_marker()
        super().deactivate()
