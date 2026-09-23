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


class _DrawTool(QgsMapTool):
    """Shared polygon/line digitising behaviour."""

    drawing_cancelled = pyqtSignal()
    _geometry_type = None
    _minimum_points = 0

    def __init__(self, canvas):
        super().__init__(canvas)
        self._points = []
        self._markers = []
        self._cursor = None
        self._band = QgsRubberBand(canvas, self._geometry_type)
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
        self._band.reset(self._geometry_type)
        points = self._points + ([cursor_pt] if cursor_pt else [])
        for index, point in enumerate(points):
            self._band.addPoint(point, index == len(points) - 1)

    def canvasMoveEvent(self, event):
        if self._points:
            self._cursor = self._snap(event.pos())
            self._redraw(self._cursor)

    def canvasPressEvent(self, event):
        if event.button() not in (_LeftButton, _RightButton):
            return
        point = self._snap(event.pos())
        self._points.append(point)
        self._add_marker(point)
        if event.button() == _RightButton:
            self._finish()
        else:
            self._redraw(self._cursor)

    def canvasDoubleClickEvent(self, event):
        self._finish()

    def keyPressEvent(self, event):
        if event.key() == _Key_Escape:
            self._reset()
            self.drawing_cancelled.emit()
        elif event.key() in (_Key_Backspace, _Key_Delete):
            self._undo_last()

    def _add_marker(self, point):
        marker = QgsVertexMarker(self.canvas())
        marker.setCenter(point)
        marker.setIconType(_IconBox)
        marker.setColor(QColor(255, 20, 147))
        marker.setFillColor(QColor(255, 255, 255, 200))
        marker.setIconSize(8)
        marker.setPenWidth(2)
        self._markers.append(marker)

    def _remove_markers(self):
        for marker in self._markers:
            self.canvas().scene().removeItem(marker)
        self._markers.clear()

    def _undo_last(self):
        if not self._points:
            return
        self._points.pop()
        if self._markers:
            self.canvas().scene().removeItem(self._markers.pop())
        self._redraw(self._cursor)

    def _finish(self):
        if len(self._points) >= self._minimum_points:
            geometry = self._geometry()
            self._reset()
            self._emit_completed(geometry)
        else:
            self._reset()

    def _reset(self):
        self._points.clear()
        self._cursor = None
        self._band.reset(self._geometry_type)
        self._remove_markers()

    def deactivate(self):
        self._reset()
        super().deactivate()


class PolygonDrawTool(_DrawTool):
    """Interactive snapped polygon drawing tool."""

    polygon_completed = pyqtSignal(object)
    _geometry_type = _PolygonGeometry
    _minimum_points = 3

    def __init__(self, canvas):
        super().__init__(canvas)
        self._band.setColor(QColor(255, 20, 147, 80))
        self._band.setStrokeColor(QColor(255, 20, 147, 220))

    def _geometry(self):
        return QgsGeometry.fromPolygonXY([list(self._points)])

    def _emit_completed(self, geometry):
        self.polygon_completed.emit(geometry)


class LineDrawTool(_DrawTool):
    """Interactive snapped line drawing tool."""

    line_completed = pyqtSignal(object)
    _geometry_type = _LineGeometry
    _minimum_points = 2

    def __init__(self, canvas):
        super().__init__(canvas)
        self._band.setColor(QColor(255, 20, 147, 220))

    def _geometry(self):
        return QgsGeometry.fromPolylineXY(list(self._points))

    def _emit_completed(self, geometry):
        self.line_completed.emit(geometry)


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
