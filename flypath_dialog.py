import datetime
import math
import os
import re
import shutil
import subprocess  # nosec B404
import tempfile
import zipfile

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QGroupBox, QScrollArea, QFrame,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QRadioButton, QButtonGroup,
    QMessageBox, QFileDialog, QApplication,
    QStackedWidget, QDialog, QTreeWidget, QTreeWidgetItem, QDialogButtonBox,
    QGraphicsView, QGraphicsScene,
)
from qgis.PyQt.QtCore import (
    Qt, QObject, QEvent, QSettings, QVariant, QSize, QPointF, pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QFont, QPixmap, QPainter, QPen, QPolygonF

try:
    _AlignLeft    = Qt.AlignmentFlag.AlignLeft
    _AlignVCenter = Qt.AlignmentFlag.AlignVCenter
    _AlignCenter  = Qt.AlignmentFlag.AlignCenter
    _EventEnter   = QEvent.Type.Enter
    _EventLeave   = QEvent.Type.Leave
    _EventResize  = QEvent.Type.Resize
    _WA_MouseTransparent = Qt.WidgetAttribute.WA_TransparentForMouseEvents
    _FrameNoFrame = QFrame.Shape.NoFrame
    _FontBold     = QFont.Weight.Bold
    _WaitCursor   = Qt.CursorShape.WaitCursor
    _KeepAspect   = Qt.AspectRatioMode.KeepAspectRatio
    _SmoothTrans  = Qt.TransformationMode.SmoothTransformation
    _ScrollHandDrag  = QGraphicsView.DragMode.ScrollHandDrag
    _AnchorUnderMouse = QGraphicsView.ViewportAnchor.AnchorUnderMouse
    _Antialias    = QPainter.RenderHint.Antialiasing
    _MB_YES       = QMessageBox.StandardButton.Yes
    _MB_NO        = QMessageBox.StandardButton.No
    _DBB_OK       = QDialogButtonBox.StandardButton.Ok
    _DBB_CANCEL   = QDialogButtonBox.StandardButton.Cancel
except AttributeError:
    # Old PyQt5 without scoped enums; fetch unscoped names dynamically so the
    # scoped forms above remain the only static enum references in the file.
    _AlignLeft    = getattr(Qt, 'AlignLeft')
    _AlignVCenter = getattr(Qt, 'AlignVCenter')
    _AlignCenter  = getattr(Qt, 'AlignCenter')
    _EventEnter   = getattr(QEvent, 'Enter')
    _EventLeave   = getattr(QEvent, 'Leave')
    _EventResize  = getattr(QEvent, 'Resize')
    _WA_MouseTransparent = getattr(Qt, 'WA_TransparentForMouseEvents')
    _FrameNoFrame = getattr(QFrame, 'NoFrame')
    _FontBold     = getattr(QFont, 'Bold')
    _WaitCursor   = getattr(Qt, 'WaitCursor')
    _KeepAspect   = getattr(Qt, 'KeepAspectRatio')
    _SmoothTrans  = getattr(Qt, 'SmoothTransformation')
    _ScrollHandDrag   = getattr(QGraphicsView, 'ScrollHandDrag')
    _AnchorUnderMouse = getattr(QGraphicsView, 'AnchorUnderMouse')
    _Antialias    = getattr(QPainter, 'Antialiasing')
    _MB_YES       = getattr(QMessageBox, 'Yes')
    _MB_NO        = getattr(QMessageBox, 'No')
    _DBB_OK       = getattr(QDialogButtonBox, 'Ok')
    _DBB_CANCEL   = getattr(QDialogButtonBox, 'Cancel')

from qgis.core import (
    Qgis,
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
    QgsDistanceArea,
)
from .map_tools import PolygonDrawTool
from .grid_planner import (
    generate_flight_grid, find_optimal_direction, split_waypoints
)
from .grid_route import split_by_waypoint_count
from .wpml import MissionSpec, write_mission
from .hardware import registry

try:
    _PolygonGeometry = QgsWkbTypes.GeometryType.PolygonGeometry
except AttributeError:
    _PolygonGeometry = getattr(QgsWkbTypes, 'PolygonGeometry')


# ── MTP PowerShell exit codes ─────────────────────────────────────────────
_MTP_EXIT_NAV_FAIL      = 1   # could not navigate path to waypoint folder
_MTP_EXIT_NO_UUID       = 2   # no UUID mission folder found in waypoint folder
_MTP_EXIT_UUID_MISSING  = 3   # UUID folder gone between script 1 and script 2
_MTP_EXIT_UUID_NO_OPEN  = 4   # UUID folder exists but GetFolder returned None

# ── RC auto-detection PowerShell exit codes ───────────────────────────────
_RC_EXIT_FOUND          = 0   # waypoint folder located (PATH/UUID on stdout)
_RC_EXIT_NONE           = 10  # no DJI RC / waypoint folder found on any device
_RC_EXIT_DEVICE_NO_WP   = 11  # a DJI device is connected but has no waypoint folder

# Relative path from an MTP storage volume to the DJI waypoint folder
_RC_REL_PARTS = ['Android', 'data', 'dji.go.v5', 'files', 'waypoint']

# Run PowerShell silently — no console window flashes on screen (Windows only;
# 0 elsewhere so the creationflags argument stays valid on every platform).
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# Belt-and-suspenders alongside CREATE_NO_WINDOW: force the window hidden.
try:
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0   # SW_HIDE
except Exception:
    _STARTUPINFO = None

# ── Silent MTP copy via IFileOperation ────────────────────────────────────
# Shell.CopyHere ignores FOF_NOCONFIRMATION/FOF_SILENT for MTP devices, so it
# always shows a progress window and a "replace this file?" prompt. The modern
# IFileOperation API honours those flags and overwrites silently and instantly.
# Bind the destination from the live Shell object's PIDL (MTP parsing paths are
# rejected by SHCreateItemFromParsingName).
_IFILEOP_CS = r'''using System;
using System.Runtime.InteropServices;
namespace FlyPathIFO {
  [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IShellItem {
    void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
    void GetParent(out IShellItem ppsi);
    void GetDisplayName(uint sigdnName, out IntPtr ppszName);
    void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
    void Compare(IShellItem psi, uint hint, out int piOrder);
  }
  [ComImport, Guid("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IFileOperation {
    uint Advise(IntPtr pfops, out uint pdwCookie);
    void Unadvise(uint dwCookie);
    void SetOperationFlags(uint dwOperationFlags);
    void SetProgressMessage([MarshalAs(UnmanagedType.LPWStr)] string pszMessage);
    void SetProgressDialog(IntPtr popd);
    void SetProperties(IntPtr pproparray);
    void SetOwnerWindow(IntPtr hwndOwner);
    void ApplyPropertiesToItem(IShellItem psiItem);
    void ApplyPropertiesToItems(IntPtr punkItems);
    void RenameItem(IShellItem psiItem, [MarshalAs(UnmanagedType.LPWStr)] string pszNewName, IntPtr pfopsItem);
    void RenameItems(IntPtr pUnkItems, [MarshalAs(UnmanagedType.LPWStr)] string pszNewName);
    void MoveItem(IShellItem psiItem, IShellItem psiDestinationFolder, [MarshalAs(UnmanagedType.LPWStr)] string pszNewName, IntPtr pfopsItem);
    void MoveItems(IntPtr punkItems, IShellItem psiDestinationFolder);
    void CopyItem(IShellItem psiItem, IShellItem psiDestinationFolder, [MarshalAs(UnmanagedType.LPWStr)] string pszCopyName, IntPtr pfopsItem);
    void CopyItems(IntPtr punkItems, IShellItem psiDestinationFolder);
    void DeleteItem(IShellItem psiItem, IntPtr pfopsItem);
    void DeleteItems(IntPtr punkItems);
    void NewItem(IShellItem psiDestinationFolder, uint dwFileAttributes, [MarshalAs(UnmanagedType.LPWStr)] string pszName, [MarshalAs(UnmanagedType.LPWStr)] string pszTemplateName, IntPtr pfopsItem);
    void PerformOperations();
    void GetAnyOperationsAborted([MarshalAs(UnmanagedType.Bool)] out bool pfAnyOperationsAborted);
  }
  public static class Op {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHCreateItemFromParsingName([MarshalAs(UnmanagedType.LPWStr)] string pszPath, IntPtr pbc, [In] ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IShellItem ppv);
    [DllImport("shell32.dll", PreserveSig = false)]
    static extern void SHGetIDListFromObject([MarshalAs(UnmanagedType.IUnknown)] object punk, out IntPtr ppidl);
    [DllImport("shell32.dll", PreserveSig = false)]
    static extern void SHCreateItemFromIDList(IntPtr pidl, [In] ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IShellItem ppv);
    static Guid IID = new Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe");
    static Guid CLSID = new Guid("3ad05575-8857-4850-9277-11b85bdb8e09");
    static IFileOperation New() {
      var op = (IFileOperation)Activator.CreateInstance(Type.GetTypeFromCLSID(CLSID));
      op.SetOperationFlags(1556); // FOF_SILENT|FOF_NOCONFIRMATION|FOF_NOERRORUI|FOF_NOCONFIRMMKDIR
      return op;
    }
    static IShellItem FromObject(object o) {
      IntPtr pidl; SHGetIDListFromObject(o, out pidl);
      Guid g = IID; IShellItem si; SHCreateItemFromIDList(pidl, ref g, out si);
      Marshal.FreeCoTaskMem(pidl);
      return si;
    }
    public static string CopyTo(string srcFile, object destFolderObj, string copyName) {
      string step = "src";
      try {
        Guid a = IID; IShellItem src; SHCreateItemFromParsingName(srcFile, IntPtr.Zero, ref a, out src);
        step = "dst"; IShellItem dst = FromObject(destFolderObj);
        step = "copy"; var op = New(); op.CopyItem(src, dst, copyName, IntPtr.Zero); op.PerformOperations();
        Marshal.ReleaseComObject(op);
        return "OK";
      } catch (Exception e) { return "ERR@" + step + ": " + e.Message; }
    }
  }
}'''

_IFILEOP_PS = "Add-Type -Language CSharp -TypeDefinition @'\n" + _IFILEOP_CS + "\n'@\n"

# ── Battery reserve ───────────────────────────────────────────────────────
# Fraction of each battery held in reserve when estimating how many batteries a
# mission needs. Manufacturer "battery_time_min" figures are ideal hover-to-empty
# numbers with no wind, turnarounds, or landing margin, so mapping plans are made
# against usable time = rated * (1 - reserve).
# Drone and camera specifications live in hardware/drones.json and are read via
# `registry` (see the hardware package).
_BATTERY_RESERVE = 0.30

# Full-automatic capture stops at every photo waypoint. Each stop costs a halt
# (decelerate + settle + accelerate, ~2.5 s from real DJI waypoint missions) or
# the camera's shutter/write time, whichever is longer, on top of transit time.
_HALT_S = 2.5
# Default waypoints per full-auto sub-mission (DJI caps a mission at ~200);
# smaller keeps each mission quick and manageable. User adjustable.
_DEFAULT_MAX_WAYPOINTS = 70

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
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {
    image: url(ARROW_UP_PATH); width: 8px; height: 5px;
}
QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {
    image: url(ARROW_DOWN_PATH); width: 8px; height: 5px;
}
QCheckBox {
    color: #D0D0D0;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid #D0D0D0;
    border-radius: 3px;
    background-color: #2A2D35;
}
QCheckBox::indicator:hover { border: 1px solid #7FB3E8; }
QCheckBox::indicator:checked {
    background-color: #2D6DB5;
    border: 1px solid #7FB3E8;
}
QRadioButton {
    color: #D0D0D0;
    spacing: 6px;
}
QRadioButton::indicator {
    width: 14px; height: 14px;
    border: 1px solid #D0D0D0;
    border-radius: 8px;
    background-color: #2A2D35;
}
QRadioButton::indicator:hover { border: 1px solid #7FB3E8; }
QRadioButton::indicator:checked {
    background-color: #2D6DB5;
    border: 1px solid #7FB3E8;
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
QPushButton#editPolygonBtn {
    background-color: #203A5A; color: #88BBFF;
    border: 1px solid #305A7A; border-radius: 3px;
    font-weight: bold; padding: 3px 6px;
}
QPushButton#editPolygonBtn:hover { background-color: #254A7A; color: #AACCFF; }
QPushButton#useSelectionBtn {
    background-color: #2A3A2A; color: #80C880;
    border: 1px solid #3A5A3A; border-radius: 3px;
    font-weight: normal; padding: 4px 8px;
}
QPushButton#useSelectionBtn:hover { background-color: #354535; color: #AAFAAA; }
QScrollArea { border: none; background-color: transparent; }
QScrollBar:vertical {
    background-color: #2A2D35; width: 7px; border-radius: 3px;
}
QScrollBar::handle:vertical {
    background-color: #4A4D55; border-radius: 3px; min-height: 20px;
}
QScrollBar::handle:vertical:hover  { background-color: #5A5D65; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLabel#areaLabel,
QLabel#frontOverlapLabel,
QLabel#flightTimeLabel, QLabel#distanceLabel, QLabel#photosLabel,
QLabel#linesLabel, QLabel#batteriesLabel, QLabel#coverageLabel {
    color: #F0A500; font-weight: bold;
}
QLabel#frontOverlapWarnLabel {
    color: #E05050; font-weight: bold;
}
QLabel#cameraInfoLabel { color: #7FB3E8; font-size: 10px; }
QWidget#actionBar {
    border-top: 1px solid #3A3D45; background-color: #181B22;
}
QPushButton#rcBrowseBtn {
    background-color: #2A3A4A; color: #7FB3E8;
    font-weight: normal; font-size: 10px; padding: 3px 6px;
    border-radius: 3px; border: 1px solid #3A5A7A;
}
QPushButton#rcBrowseBtn:hover { background-color: #354A5E; }
QLabel#rcNote {
    color: #7FB3E8;
    font-size: 10px;
    padding: 4px 6px;
    background-color: #1A2530;
    border-left: 3px solid #2D6DB5;
    border-radius: 3px;
}
QLabel#rcThumb {
    color: #6A7686;
    font-size: 10px;
    background-color: #15181E;
    border: 1px solid #2A2D35;
    border-radius: 3px;
    padding: 2px;
}
QLabel#infoBar {
    color: #7FB3E8;
    font-size: 10px;
    padding: 4px 8px 4px 10px;
    background-color: #1A1D24;
    border-top: 1px solid #2A2D35;
    border-left: 3px solid #2D6DB5;
}
QLabel#infoBarIdle {
    color: #4A5568;
    font-size: 10px;
    padding: 4px 8px 4px 10px;
    background-color: #1A1D24;
    border-top: 1px solid #2A2D35;
    border-left: 3px solid #3A3D45;
}
"""


_INFO_IDLE = 'ⓘ  Hover over any field to see what it does.'

# ── Map preview colour constants ───────────────────────────────────────────
_COLOR_START_MARKER  = '#CC2222'   # red filled circle — first waypoint
_COLOR_END_MARKER    = '#2D6DB5'   # blue filled circle — last waypoint
_COLOR_MID_MARKER    = 'white'     # white circle — intermediate waypoints

# Distinct flight-path colours, one per split mission. The first is the same
# yellow used before splitting existed, so an unsplit plan looks unchanged.
_MISSION_COLORS = [
    '#FFE600', '#00E0FF', '#FF7AD9', '#7CFF6B', '#FFA24B',
    '#B98CFF', '#4BE0C0', '#FF6B6B', '#8CD6FF', '#E0FF6B',
]


def _mission_color(i):
    return _MISSION_COLORS[i % len(_MISSION_COLORS)]


class _HoverFilter(QObject):
    """Event filter that writes a hint to a shared info label on mouse enter/leave."""

    def __init__(self, label, text, parent=None):
        super().__init__(parent)
        self._label = label
        self._text  = text

    def eventFilter(self, obj, event):
        if event.type() == _EventEnter:
            self._label.setObjectName('infoBar')
            self._label.setStyleSheet('')   # re-apply via object name
            self._label.setText('ⓘ  ' + self._text)
            self._label.style().unpolish(self._label)
            self._label.style().polish(self._label)
        elif event.type() == _EventLeave:
            self._label.setObjectName('infoBarIdle')
            self._label.setStyleSheet('')
            self._label.setText(_INFO_IDLE)
            self._label.style().unpolish(self._label)
            self._label.style().polish(self._label)
        return False   # never consume the event


# Item-data roles for the shell browser (plain ints: binding-agnostic, and
# Qt.UserRole is always 0x0100).
_ROLE_PARTS  = 256   # the list of folder names from "This PC" to this item
_ROLE_LOADED = 257   # whether this item's children have been fetched yet


class _RcFolderBrowser(QDialog):
    """
    A folder picker that browses the Windows shell namespace, so it can reach
    MTP devices (the DJI RC) which the standard folder dialog cannot show.

    Children are loaded lazily (one shell scan per expand) via the supplied
    list_children(parts) -> [name, ...] callable.
    """

    def __init__(self, list_children, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Select the RC waypoint folder')
        self._list_children = list_children

        v = QVBoxLayout(self)
        hint = QLabel(
            'Browse to the waypoint folder, then click Select. On the RC it is:'
            '\nDJI RC › Internal shared storage › Android › data '
            '› dji.go.v5 › files › waypoint'
        )
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self._on_expand)
        self.tree.currentItemChanged.connect(self._on_sel)
        v.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(_DBB_OK | _DBB_CANCEL)
        self._ok = buttons.button(_DBB_OK)
        self._ok.setText('Select')
        self._ok.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

        self.resize(440, 480)
        self._add_children(None, [])   # populate "This PC"

    def _add_children(self, parent_item, parts):
        QApplication.setOverrideCursor(_WaitCursor)
        try:
            names = self._list_children(parts)
        except Exception:
            names = []
        finally:
            QApplication.restoreOverrideCursor()
        for name in names:
            item = QTreeWidgetItem([name])
            item.setData(0, _ROLE_PARTS, parts + [name])
            item.setData(0, _ROLE_LOADED, False)
            item.addChild(QTreeWidgetItem(['…']))   # dummy → shows arrow
            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)

    def _on_expand(self, item):
        if item.data(0, _ROLE_LOADED):
            return
        item.setData(0, _ROLE_LOADED, True)
        item.takeChildren()                       # drop the dummy
        self._add_children(item, item.data(0, _ROLE_PARTS))

    def _on_sel(self, current, _previous):
        self._ok.setEnabled(bool(current) and current.data(0, _ROLE_PARTS) is not None)

    def selected_parts(self):
        item = self.tree.currentItem()
        return item.data(0, _ROLE_PARTS) if item else None


class _ClickableLabel(QLabel):
    """A QLabel that emits `clicked` when it holds an image and is pressed."""
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        pm = self.pixmap()
        if pm is not None and not pm.isNull():
            self.clicked.emit()
        super().mousePressEvent(event)


class _ZoomView(QGraphicsView):
    """QGraphicsView with mouse-wheel zoom (drag-to-pan is set by the caller)."""

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.scale(factor, factor)


class _ThumbnailViewer(QDialog):
    """A resizable window showing the full mission image with zoom and pan."""

    def __init__(self, image_path, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._view = _ZoomView()
        self._view.setDragMode(_ScrollHandDrag)
        self._view.setTransformationAnchor(_AnchorUnderMouse)
        scene = QGraphicsScene(self)
        self._item = scene.addPixmap(QPixmap(image_path))
        self._item.setTransformationMode(_SmoothTrans)
        self._view.setScene(scene)
        layout.addWidget(self._view, 1)

        hint = QLabel('Scroll to zoom  ·  drag to pan')
        hint.setAlignment(_AlignCenter)
        layout.addWidget(hint)

    def showEvent(self, event):
        super().showEvent(event)
        self._view.fitInView(self._item, _KeepAspect)


class FlyPathDialog(QWidget):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface

        # State
        self._hud                = None   # flight-stats card overlaid on the map
        self._survey_polygon     = None
        self._survey_polygon_crs = None
        self._draw_tool          = None
        self._selected_layer_id  = None   # layer carrying the current map selection
        self._monitored_layer_id = None   # layer whose edit signals we're connected to
        self._prev_map_tool      = None
        self._syncing_selection  = False  # guard: prevent selectByIds re-entrance
        self._survey_area_layer_id = None  # temporary drawn-polygon layer
        self._preview_layer_ids  = []     # [path_line_id, waypoints_id]
        self._waypoints          = []
        self._shot_spacing_m     = 0.0
        self._missions           = []     # last previewed plan, split into sub-missions
        self._live_waypoints     = None   # last grid computed for stats/live sync
        self._live_missions      = None   # last grid split by the current split count
        self._split_overridden   = False  # user typed a split count; stop auto-tracking
        self._setting_split      = False  # guard: programmatic splitSpin.setValue()
        self._rc_waypoint_path   = None   # detected RC waypoint folder display path
        self._thumb_dir          = None   # session cache for pulled RC mission thumbnails
        self._current_thumb_full = None   # full-res preview path for the zoom viewer
        self._render_cache       = {}     # uuid -> rendered waypoint preview path

        self._build_ui()
        self._setup_combos()
        self._connect_signals()
        self._update_camera_info()
        self._apply_speed_range()
        self._apply_drone_capabilities()
        self._update_gsd()
        self._update_interval()

    # ── Hover-hint helper ─────────────────────────────────────────────────

    def _tip(self, widget, text):
        """Attach a hover hint to widget — shown in the info bar, not as a tooltip."""
        widget.setToolTip('')   # disable floating tooltip
        f = _HoverFilter(self.infoBar, text, parent=widget)
        widget.installEventFilter(f)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        arrow_down = os.path.join(plugin_dir, 'arrow_down.svg').replace('\\', '/')
        arrow_up   = os.path.join(plugin_dir, 'arrow_up.svg').replace('\\', '/')
        self.setStyleSheet(
            STYLESHEET
            .replace('ARROW_DOWN_PATH', arrow_down)
            .replace('ARROW_UP_PATH',   arrow_up)
        )

        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        # Scrollable form area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_FrameNoFrame)

        content = QWidget()
        scroll_layout = QVBoxLayout(content)
        scroll_layout.setSpacing(8)
        scroll_layout.setContentsMargins(8, 8, 8, 8)

        # Info bar must exist before group builders call self._tip()
        self.infoBar = QLabel(_INFO_IDLE)
        self.infoBar.setObjectName('infoBarIdle')
        self.infoBar.setWordWrap(True)
        self.infoBar.setMinimumHeight(32)

        scroll_layout.addWidget(self._build_mission_group())
        scroll_layout.addWidget(self._build_area_group())

        # Flight Parameters (left) beside Adv. Mission Organizers + Safety
        # Actions (right).
        params_row = QHBoxLayout()
        params_row.setSpacing(8)

        # Gimbal angle and photo interval are fixed for 2D mapping and do not
        # affect the waypoints, so they are kept internally rather than shown.
        self._init_internal_camera_widgets()

        flight_col = QVBoxLayout()
        flight_col.setSpacing(8)
        flight_col.addWidget(self._build_flight_group())
        flight_col.addStretch()

        # Right column: how the grid becomes deliverable missions, then safety.
        right_col = QVBoxLayout()
        right_col.setSpacing(8)
        right_col.addWidget(self._build_organizer_group())
        right_col.addWidget(self._build_advanced_group())
        right_col.addStretch()

        params_row.addLayout(flight_col, 1)
        params_row.addLayout(right_col, 1)
        scroll_layout.addLayout(params_row)

        # Statistics live in a HUD card overlaid on the map canvas (outside the
        # panel), shown when a plan exists. Built here so the stat labels exist
        # for _update_stats.
        self._hud = self._build_stats_hud()
        scroll_layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.infoBar)
        outer.addWidget(self._build_action_bar())

        # Let the dock shrink only down to the natural minimum width of its
        # content (where the two-column sections still fit), not smaller. The
        # scroll area would otherwise let the content be squeezed indefinitely.
        content.layout().activate()
        # + vertical scrollbar allowance so the content is not clipped at min;
        # small floor guards against a degenerate hint before the first show.
        min_w = content.minimumSizeHint().width() + 20
        self.setMinimumWidth(max(min_w, 280))

    def _build_mission_group(self):
        group = QGroupBox('Mission Setup')
        form  = QFormLayout(group)
        form.setLabelAlignment(_AlignLeft | _AlignVCenter)
        form.setSpacing(6)

        self.missionTypeCombo = QComboBox()
        self.missionTypeCombo.addItem('2D Mapping')
        self._tip(self.missionTypeCombo,
            'The kind of mission to plan. 2D Mapping flies a grid over the '
            'survey area for orthomosaic mapping. More mission types will be '
            'added here.')
        form.addRow('Mission Type', self.missionTypeCombo)

        # Capture mode for mapping missions: how the camera is triggered.
        self.captureSemiRadio = QRadioButton('Semi')
        self.captureFullRadio = QRadioButton('Full')
        self.captureSemiRadio.setChecked(True)
        self._captureGroup = QButtonGroup(self)
        self._captureGroup.addButton(self.captureSemiRadio)
        self._captureGroup.addButton(self.captureFullRadio)
        self._tip(self.captureSemiRadio,
            'Semi-automatic: flies a grid of flight lines; you set the camera to '
            'interval capture before takeoff.')
        self._tip(self.captureFullRadio,
            'Full-automatic: places a waypoint at every photo and the drone '
            'shoots automatically (stop-and-shoot), so no manual interval is '
            'needed. Slower and uses more battery because the drone stops at '
            'each photo.')
        capture_row = QWidget()
        capture_layout = QHBoxLayout(capture_row)
        capture_layout.setContentsMargins(0, 0, 0, 0)
        capture_layout.setSpacing(12)
        capture_layout.addWidget(self.captureSemiRadio)
        capture_layout.addWidget(self.captureFullRadio)
        capture_layout.addStretch()
        self._captureRow = capture_row
        form.addRow('Capture', capture_row)

        self.droneModelCombo = QComboBox()
        self._tip(self.droneModelCombo,
            'Your drone model — determines camera sensor specs, '
            'maximum speed, and battery endurance used in all calculations.')
        form.addRow('Drone', self.droneModelCombo)

        self.cameraInfoLabel = QLabel('—')
        self.cameraInfoLabel.setObjectName('cameraInfoLabel')
        self._tip(self.cameraInfoLabel,
            'Camera sensor summary for the selected drone. '
            'Used to calculate GSD, footprint size, and photo interval.')
        form.addRow('Camera', self.cameraInfoLabel)

        return group

    def _build_area_group(self):
        group = QGroupBox('Survey Area')
        form  = QFormLayout(group)
        form.setLabelAlignment(_AlignLeft | _AlignVCenter)
        form.setSpacing(6)

        self.layerCombo = QComboBox()
        self._tip(self.layerCombo,
            'Select a polygon layer from the QGIS project. '
            'Only polygon layers are listed. '
            'For multi-feature layers a Feature selector will appear below.')
        form.addRow('Layer', self.layerCombo)

        self.featureCombo = QComboBox()
        self.featureCombo.setVisible(False)
        self._tip(self.featureCombo,
            'Select which polygon feature to use as the survey area '
            'when the chosen layer contains more than one feature.')
        self._featureComboRow = form.rowCount()
        form.addRow('Feature', self.featureCombo)

        self.useSelectionBtn = QPushButton('Use QGIS Selection')
        self.useSelectionBtn.setObjectName('useSelectionBtn')
        self._tip(self.useSelectionBtn,
            'Adopt the polygon currently selected with the QGIS selection tool. '
            'Exactly one polygon must be selected across all layers.')
        form.addRow(self.useSelectionBtn)

        draw_row = QWidget()
        draw_layout = QHBoxLayout(draw_row)
        draw_layout.setContentsMargins(0, 0, 0, 0)
        draw_layout.setSpacing(4)

        self.drawPolygonBtn = QPushButton('Draw Polygon on Map')
        self.drawPolygonBtn.setObjectName('drawPolygonBtn')
        self.drawPolygonBtn.setCheckable(True)
        self._tip(self.drawPolygonBtn,
            'Activate the polygon drawing tool. '
            'Left-click to place vertices, right-click or double-click to finish, '
            'Backspace to undo the last vertex, Escape to cancel.')

        self.editPolygonBtn = QPushButton('✎ Edit')
        self.editPolygonBtn.setObjectName('editPolygonBtn')
        self._tip(self.editPolygonBtn,
            'Edit the drawn polygon: drag a vertex to move it, click an edge '
            'to add a vertex, or select a vertex and press Delete to remove '
            'it. Click Finish Editing when done.')
        self.editPolygonBtn.setVisible(False)

        self.removePolygonBtn = QPushButton('✕ Remove')
        self.removePolygonBtn.setObjectName('removePolygonBtn')
        self._tip(self.removePolygonBtn,
            'Remove the drawn polygon and reset the survey area.')
        self.removePolygonBtn.setVisible(False)

        draw_layout.addWidget(self.drawPolygonBtn)
        draw_layout.addWidget(self.editPolygonBtn)
        draw_layout.addWidget(self.removePolygonBtn)
        form.addRow(draw_row)

        self.areaLabel = QLabel('—')
        self.areaLabel.setObjectName('areaLabel')
        self._tip(self.areaLabel, 'Total area of the survey polygon in hectares.')
        form.addRow('Area', self.areaLabel)

        return group

    def _build_flight_group(self):
        group = QGroupBox('Flight Parameters')
        form  = QFormLayout(group)
        self._flight_form = form   # kept so mission-type logic can hide rows
        form.setLabelAlignment(_AlignLeft | _AlignVCenter)
        form.setSpacing(6)

        self.altitudeSpin = QDoubleSpinBox()
        self.altitudeSpin.setRange(30.0, 500.0)
        self.altitudeSpin.setValue(100.0)
        self.altitudeSpin.setSingleStep(5.0)
        self.altitudeSpin.setDecimals(1)
        self.altitudeSpin.setSuffix(' m')
        self._tip(self.altitudeSpin,
            'Flight altitude above ground level (AGL). '
            'Higher altitude → wider coverage per photo but lower resolution (larger GSD). '
            'Lower altitude → sharper images but more flight lines and longer mission time.')
        form.addRow('Altitude', self.altitudeSpin)

        self.gsdSpin = QDoubleSpinBox()
        self.gsdSpin.setRange(0.10, 20.0)
        self.gsdSpin.setValue(1.0)
        self.gsdSpin.setSingleStep(0.1)
        self.gsdSpin.setDecimals(2)
        self.gsdSpin.setSuffix(' cm/px')
        self._tip(self.gsdSpin,
            'Ground Sampling Distance: the real-world size of one pixel in your photos. '
            'Smaller GSD means higher resolution. Linked to altitude, so changing one '
            'updates the other (and the flight path when you preview).')
        form.addRow('GSD', self.gsdSpin)

        self.sideOverlapSpin = QSpinBox()
        self.sideOverlapSpin.setRange(50, 95)
        self.sideOverlapSpin.setValue(70)
        self.sideOverlapSpin.setSuffix(' %')
        self._tip(self.sideOverlapSpin,
            'How much adjacent flight lines overlap each other. '
            'Higher → fewer gaps, better stitching, but more flight lines. '
            'Recommended: 60–75% for flat terrain, 70–80% for hilly terrain.')
        form.addRow('Side Overlap', self.sideOverlapSpin)

        # Front (along-track) overlap sits with Side Overlap since both are
        # overlap settings. It is one row that swaps content by mode via a
        # stacked widget: a derived read-out in semi-automatic, a direct input in
        # full-automatic. (One row, so hiding a mode leaves no blank gap.)
        self.frontOverlapSpin = QSpinBox()
        self.frontOverlapSpin.setRange(50, 95)
        self.frontOverlapSpin.setValue(70)
        self.frontOverlapSpin.setSingleStep(5)
        self.frontOverlapSpin.setSuffix(' %')
        self._tip(self.frontOverlapSpin,
            'Target front (along-track) overlap between consecutive photos. '
            'A waypoint is placed every footprint x (1 - overlap) metres along '
            'each line. Aim for 75-85% for mapping, 85-90% for 3D models.')

        self.frontOverlapLabel = QLabel('—')
        self.frontOverlapLabel.setObjectName('frontOverlapLabel')
        self.frontOverlapLabel.setAlignment(_AlignLeft | _AlignVCenter)
        self._tip(self.frontOverlapLabel,
            'Calculated front overlap between consecutive photos along the flight line. '
            'Based on speed, interval, altitude and drone sensor. '
            'Aim for 75–85% for mapping, 85–90% for 3D models. '
            'Reduce speed or increase interval to raise overlap.')

        self.frontOverlapStack = QStackedWidget()
        self.frontOverlapStack.addWidget(self.frontOverlapLabel)   # index 0: semi
        self.frontOverlapStack.addWidget(self.frontOverlapSpin)    # index 1: full
        form.addRow('Front Overlap', self.frontOverlapStack)

        self.speedSpin = QDoubleSpinBox()
        self.speedSpin.setRange(1.0, 12.0)
        self.speedSpin.setValue(5.0)
        self.speedSpin.setSingleStep(0.5)
        self.speedSpin.setDecimals(1)
        self.speedSpin.setSuffix(' m/s')
        self._tip(self.speedSpin,
            'Drone flight speed during the mission. '
            'Higher speed → shorter mission time but may cause motion blur. '
            'Keep speed low enough for your shutter speed at the chosen altitude.')
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
        self._tip(self.directionSpin,
            'Flight line direction in degrees clockwise from North. '
            '0° = North–South lines, 90° = East–West lines. '
            'Align with the longest axis of the polygon to minimise turns.')

        self.autoDirectionBtn = QPushButton('Auto')
        self.autoDirectionBtn.setObjectName('autoDirectionBtn')
        self.autoDirectionBtn.setFixedWidth(52)
        self._tip(self.autoDirectionBtn,
            'Automatically find the optimal flight direction '
            'that minimises the number of flight lines for this polygon.')

        dir_layout.addWidget(self.directionSpin)
        dir_layout.addWidget(self.autoDirectionBtn)
        form.addRow('Direction', dir_widget)

        self.marginSpin = QDoubleSpinBox()
        self.marginSpin.setRange(0.0, 200.0)
        self.marginSpin.setValue(0.0)
        self.marginSpin.setSingleStep(5.0)
        self.marginSpin.setDecimals(1)
        self.marginSpin.setSuffix(' m')
        self._tip(self.marginSpin,
            'Buffer distance added around the survey polygon boundary. '
            'Ensures full photo coverage at the edges. '
            'Typically 0–50 m depending on altitude and terrain.')
        form.addRow('Margin', self.marginSpin)

        return group

    def _build_organizer_group(self):
        """Adv. Mission Organizers — how the planned grid is turned into the
        deliverable mission files: how many to split it into, the per-mission
        waypoint cap, and whether to add a perpendicular cross-hatch pass."""
        group = QGroupBox('Adv. Mission Organizers')
        form  = QFormLayout(group)
        self._organizer_form = form   # kept so capability logic can hide rows
        form.setLabelAlignment(_AlignLeft | _AlignVCenter)
        form.setSpacing(6)

        self.splitSpin = QSpinBox()
        self.splitSpin.setRange(1, 99)
        self.splitSpin.setValue(1)
        self._tip(self.splitSpin,
            'Minimum number of separate missions to split the survey into, each '
            'flown on its own battery and exported as its own KMZ file. Defaults '
            'to the estimated number of batteries; raise it to fly more, smaller '
            'missions. In full-automatic mode this is a minimum: the Max '
            'Waypoints cap may create more missions than this, never fewer.')
        form.addRow('Split Missions', self.splitSpin)

        self.maxWaypointsSpin = QSpinBox()
        self.maxWaypointsSpin.setRange(2, 400)
        self.maxWaypointsSpin.setValue(_DEFAULT_MAX_WAYPOINTS)
        self.maxWaypointsSpin.setMaximumWidth(110)
        self._tip(self.maxWaypointsSpin,
            'Maximum waypoints per mission. DJI caps a mission at about 200 '
            'waypoints, so the survey is split so no mission exceeds this. '
            'Full-automatic places one waypoint per photo; semi-automatic uses '
            'two per flight line. Lower it for shorter, more manageable missions.')
        form.addRow('Max Waypoints', self.maxWaypointsSpin)

        self.crossHatchCheck = QCheckBox('Cross-hatch')
        self._tip(self.crossHatchCheck,
            'Fly the grid, then fly it again perpendicular to the flight '
            'direction. The double coverage improves 3D reconstruction and, for '
            'LiDAR, point-cloud stability. It roughly doubles flight time, photos '
            'and battery use.')
        form.addRow('', self.crossHatchCheck)

        return group

    def _init_internal_camera_widgets(self):
        """Camera settings are constant for 2D mapping and do not change the
        waypoints or flight route, so they are kept as internal (non-UI) widgets
        rather than a Camera Settings section:
          - Gimbal Angle stays nadir (-90 degrees), written into every mission.
          - Photo Interval holds the shutter floor, feeding the semi-auto front
            overlap read-out and photo-count estimate.
        Both remain live objects so the existing read sites keep working."""
        self.gimbalAngleSpin = QSpinBox()
        self.gimbalAngleSpin.setRange(-90, -30)
        self.gimbalAngleSpin.setValue(-90)

        self.photoIntervalSpin = QDoubleSpinBox()
        self.photoIntervalSpin.setRange(2.0, 60.0)
        self.photoIntervalSpin.setValue(2.0)

    def _build_advanced_group(self):
        group = QGroupBox('Safety Actions')
        group.setMaximumWidth(210)
        form  = QFormLayout(group)
        form.setLabelAlignment(_AlignLeft | _AlignVCenter)
        form.setSpacing(6)

        self.finishActionCombo = QComboBox()
        self.finishActionCombo.setMaximumWidth(110)
        self._tip(self.finishActionCombo,
            'What the drone does after the last waypoint. '
            'Return to Home: flies back and lands at takeoff. '
            'Hover: holds position at the last waypoint. '
            'Land: lands immediately at the last waypoint.')
        form.addRow('Finish Action', self.finishActionCombo)

        self.rcLostActionCombo = QComboBox()
        self.rcLostActionCombo.setMaximumWidth(110)
        self._tip(self.rcLostActionCombo,
            'What the drone does if the RC signal is lost during the mission. '
            'Return to Home: flies back to takeoff point. '
            'Hover: holds position and waits for signal. '
            'Land: lands immediately at current position. '
            'Continue: keeps flying the mission regardless.')
        form.addRow('RC Lost Action', self.rcLostActionCombo)

        return group

    def _build_stats_hud(self):
        """Flight-stats HUD: a compact, semi-transparent card overlaid on a
        corner of the map canvas, shown only when a plan exists. Sits by the
        flight lines, keeps the panel and toolbars free, and never shrinks the
        map. It is click-through so it never blocks map interaction. Creates the
        same label attributes _update_stats writes to."""
        fields = [
            ('flightTimeLabel', 'Flight Time',
             'Estimated total flight duration based on path length and speed. '
             'Does not include takeoff, landing, or battery swap time.'),
            ('linesLabel',      'Lines',
             'Number of parallel flight lines needed to cover the survey area.'),
            ('distanceLabel',   'Distance',
             'Total distance the drone will fly along all flight lines.'),
            ('batteriesLabel',  'Batteries',
             'Estimated battery charges needed for the mission. Planned against a '
             f'{int(round(_BATTERY_RESERVE * 100))}% reserve, so usable time per '
             f'battery is {int(round((1 - _BATTERY_RESERVE) * 100))}% of the '
             'drone\'s rated endurance, leaving margin for wind, turnarounds and '
             'a safe return.'),
            ('coverageLabel',   'Coverage',
             'Total survey area in hectares as calculated from the polygon.'),
            ('photosLabel',     'Photos',
             'Estimated number of photos the camera will take during the mission.'),
        ]
        canvas = self.iface.mapCanvas()
        try:
            hud = QFrame(canvas)                  # child of the map canvas
        except TypeError:
            hud = QFrame()                        # headless / mock fallback
        hud.setObjectName('flypathHud')
        hud.setAttribute(_WA_MouseTransparent, True)   # clicks pass to the map
        grid = QGridLayout(hud)
        grid.setContentsMargins(12, 9, 12, 9)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        title = QLabel('FlyPath')
        title.setObjectName('hudTitle')
        grid.addWidget(title, 0, 0, 1, 4)
        for i, (attr, caption, tip) in enumerate(fields):
            r = 1 + i % 3
            c = (i // 3) * 2
            cap = QLabel(f'{caption}')
            cap.setObjectName('hudCaption')
            val = QLabel('—')
            val.setObjectName(attr)
            val.setToolTip(tip)
            setattr(self, attr, val)
            grid.addWidget(cap, r, c)
            grid.addWidget(val, r, c + 1)
        hud.setStyleSheet(
            '#flypathHud { background-color: rgba(24, 27, 34, 0.86); '
            'border: 1px solid #3A3D45; border-radius: 6px; }'
            '#flypathHud QLabel { color: #C7CBD1; font-size: 11px; }'
            '#flypathHud QLabel#hudTitle { color: #7FB3E8; font-weight: bold; '
            'font-size: 10px; }'
            '#flypathHud QLabel#flightTimeLabel, #flypathHud QLabel#distanceLabel, '
            '#flypathHud QLabel#coverageLabel, #flypathHud QLabel#linesLabel, '
            '#flypathHud QLabel#batteriesLabel, #flypathHud QLabel#photosLabel '
            '{ color: #F0A500; font-weight: bold; }'
        )
        hud.hide()
        if hasattr(canvas, 'installEventFilter'):
            try:
                canvas.installEventFilter(self)   # reposition on canvas resize
            except TypeError:
                pass
        return hud

    # ── Flight-stats HUD placement / visibility ───────────────────────────

    def _position_hud(self):
        """Pin the HUD to the top-right corner of the map canvas."""
        canvas = self.iface.mapCanvas()
        if not self._hud or not hasattr(canvas, 'width'):
            return
        self._hud.adjustSize()
        margin = 12
        x = canvas.width() - self._hud.width() - margin
        self._hud.move(max(margin, int(x)), margin)

    def _show_hud(self):
        if self._hud and self.isVisible():
            self._position_hud()
            self._hud.show()
            self._hud.raise_()

    def _hide_hud(self):
        if self._hud:
            self._hud.hide()

    def eventFilter(self, obj, event):
        if (self._hud and obj is self.iface.mapCanvas()
                and event.type() == _EventResize and self._hud.isVisible()):
            self._position_hud()
        return super().eventFilter(obj, event)

    def _build_action_bar(self):
        bar = QWidget()
        bar.setObjectName('actionBar')
        layout = QVBoxLayout(bar)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 6, 8, 10)

        settings = QSettings('FlyPath', 'FlyPath')

        # ── Map actions first (you preview, then choose where it goes) ────────
        map_row = QWidget()
        map_layout = QHBoxLayout(map_row)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.setSpacing(4)

        self.previewBtn = QPushButton('Preview on Map')
        self.previewBtn.setMinimumHeight(30)
        self._tip(self.previewBtn,
            'Generate the flight grid and display the waypoint path '
            'on the QGIS map canvas for review before exporting.')

        self.clearPreviewBtn = QPushButton('Clear Preview')
        self.clearPreviewBtn.setObjectName('clearPreviewBtn')
        self.clearPreviewBtn.setMinimumHeight(30)
        self._tip(self.clearPreviewBtn,
            'Remove the flight path preview layers from the map '
            'and reset the survey area selection.')

        map_layout.addWidget(self.previewBtn, 2)
        map_layout.addWidget(self.clearPreviewBtn, 1)
        layout.addWidget(map_row)

        # ── Export section: its own group, separate from the map actions ──────
        export_group = QGroupBox('Export Mission')
        export_layout = QVBoxLayout(export_group)
        export_layout.setSpacing(6)
        export_layout.setContentsMargins(8, 8, 8, 8)

        # Destination selector
        dest_row = QWidget()
        dest_layout = QHBoxLayout(dest_row)
        dest_layout.setContentsMargins(0, 0, 0, 0)
        dest_layout.setSpacing(4)

        self.destCombo = QComboBox()
        self.destCombo.addItem('Save to computer', 'local')
        self.destCombo.addItem('Send to DJI RC',   'rc')
        self._tip(self.destCombo,
            'Choose where the mission goes: a folder on this computer, '
            'or directly onto a connected DJI RC.')

        dest_layout.addWidget(QLabel('Destination'))
        dest_layout.addWidget(self.destCombo, 1)
        export_layout.addWidget(dest_row)

        # Local / RC panels swapped by the selector
        self.destStack = QStackedWidget()
        self.destStack.addWidget(self._build_local_dest_panel(settings))
        self.destStack.addWidget(self._build_rc_dest_panel())
        export_layout.addWidget(self.destStack)

        # Export button (label adapts to the chosen destination)
        self.exportBtn = QPushButton('Export KMZ')
        self.exportBtn.setObjectName('exportBtn')
        self.exportBtn.setMinimumHeight(36)
        self._tip(self.exportBtn,
            'Save the mission to the chosen folder, or replace the selected '
            'mission on the RC, depending on the destination above.')
        export_layout.addWidget(self.exportBtn)

        layout.addWidget(export_group)

        # Restore last-used destination mode
        mode = settings.value('dest_mode', 'local')
        idx  = self.destCombo.findData(mode)
        self.destCombo.setCurrentIndex(idx if idx >= 0 else 0)
        self.destStack.setCurrentIndex(self.destCombo.currentIndex())
        self._update_export_button()

        return bar

    def _build_local_dest_panel(self, settings):
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.localFolderEdit = QLineEdit()
        self.localFolderEdit.setPlaceholderText('Folder to save the .kmz file…')
        self.localFolderEdit.setText(
            settings.value('local_export_dir', os.path.expanduser('~'))
        )
        self._tip(self.localFolderEdit,
            'Folder on this computer where the mission .kmz file will be saved.')

        self.localBrowseBtn = QPushButton('Browse…')
        self.localBrowseBtn.setObjectName('rcBrowseBtn')
        self.localBrowseBtn.setFixedWidth(64)
        self._tip(self.localBrowseBtn,
            'Choose the folder to save the mission file in.')

        row.addWidget(self.localFolderEdit)
        row.addWidget(self.localBrowseBtn)
        v.addLayout(row)
        return panel

    def _build_rc_dest_panel(self):
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        # The two ways to find the RC, side by side.
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(4)

        self.rcRefreshBtn = QPushButton('Auto Detect RC')
        self.rcRefreshBtn.setMinimumHeight(28)
        self._tip(self.rcRefreshBtn,
            'Automatically find the DJI RC, whether it is connected over USB '
            'or shows up as a removable drive, and list its missions.')

        self.rcManualBtn = QPushButton('Locate folder manually')
        self.rcManualBtn.setObjectName('rcBrowseBtn')
        self.rcManualBtn.setMinimumHeight(28)
        self._tip(self.rcManualBtn,
            'Browse This PC yourself — including the RC and any drives — and '
            'pick the waypoint folder. Use this if Auto Detect did not find it.')

        btn_row.addWidget(self.rcRefreshBtn, 1)
        btn_row.addWidget(self.rcManualBtn, 1)
        v.addLayout(btn_row)

        self.rcStatusLabel = QLabel('Press Auto Detect RC to find your controller')
        self.rcStatusLabel.setObjectName('rcStatusLabel')
        self.rcStatusLabel.setWordWrap(True)
        v.addWidget(self.rcStatusLabel)

        # Read-only display of the chosen waypoint folder (auto or manual).
        self.rcTargetEdit = QLineEdit()
        self.rcTargetEdit.setReadOnly(True)
        self.rcTargetEdit.setPlaceholderText('No folder selected yet')
        self._tip(self.rcTargetEdit,
            'The RC waypoint folder FlyPath will write the mission into. '
            'Set by Auto Detect RC or Locate folder manually.')
        v.addWidget(self.rcTargetEdit)

        # When the survey is split, choose which part goes into the RC slot.
        # The RC replaces one mission at a time, so parts are sent one by one.
        self.splitPartRow = QWidget()
        part_layout = QHBoxLayout(self.splitPartRow)
        part_layout.setContentsMargins(0, 0, 0, 0)
        part_layout.setSpacing(4)
        part_label = QLabel('Mission part')
        self.splitPartCombo = QComboBox()
        self._tip(self.splitPartCombo,
            'Which split mission to send into the selected RC slot. The RC '
            'replaces one mission at a time, so send each part in turn, each '
            'into its own RC mission.')
        part_layout.addWidget(part_label)
        part_layout.addWidget(self.splitPartCombo, 1)
        self.splitPartRow.setVisible(False)
        v.addWidget(self.splitPartRow)

        self.rcMissionCombo = QComboBox()
        self._tip(self.rcMissionCombo,
            'The mission on the RC that will be replaced when you export. '
            'Match it by date with what you see in DJI Fly.')
        v.addWidget(self.rcMissionCombo)

        # DJI's own map snapshot of the selected mission, so you can see what
        # you are about to replace. Click to open a zoomable full-size viewer.
        self.rcThumbLabel = _ClickableLabel('')
        self.rcThumbLabel.setObjectName('rcThumb')
        self.rcThumbLabel.setAlignment(_AlignCenter)
        self.rcThumbLabel.setMinimumHeight(120)
        self.rcThumbLabel.setWordWrap(True)
        self._tip(self.rcThumbLabel,
            'Flight path of the selected mission, drawn from its waypoints so it '
            'always matches the RC. Click it to open a larger, zoomable view.')
        v.addWidget(self.rcThumbLabel)

        self.rcNote = QLabel(
            'ⓘ  FlyPath replaces an existing mission. To add a new one, create '
            'it in DJI Fly first, then Auto Detect RC.')
        self.rcNote.setObjectName('rcNote')
        self.rcNote.setWordWrap(True)
        v.addWidget(self.rcNote)
        return panel

    # ── Combo population ──────────────────────────────────────────────────

    def _setup_combos(self):
        self.droneModelCombo.addItems(registry.names())
        self.droneModelCombo.setCurrentText('DJI Mini 4 Pro')


        self.finishActionCombo.addItems([
            'Return to Home',
            'Hover in place',
            'Land at last waypoint',
        ])
        self.rcLostActionCombo.addItems([
            'Return to Home',
            'Hover in place',
            'Land immediately',
            'Continue mission',
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
                    _PolygonGeometry and
                    not layer.customProperty('flypath_internal')):
                self.layerCombo.addItem(layer.name(), layer.id())
        # Restore previous selection if the layer still exists
        idx = self.layerCombo.findData(previously_selected)
        self.layerCombo.setCurrentIndex(idx if idx >= 0 else 0)
        self.layerCombo.blockSignals(False)

    # ── Signal wiring ─────────────────────────────────────────────────────

    def _connect_signals(self):
        # Refresh layer combo when layers are added/removed
        QgsProject.instance().layersAdded.connect(self._refresh_layer_combo)
        QgsProject.instance().layersRemoved.connect(self._refresh_layer_combo)

        self.missionTypeCombo.currentIndexChanged.connect(self._on_mission_type_changed)
        self.captureSemiRadio.toggled.connect(self._on_mission_type_changed)
        self.frontOverlapSpin.valueChanged.connect(self._on_param_changed)
        self.maxWaypointsSpin.valueChanged.connect(self._on_param_changed)
        self.droneModelCombo.currentIndexChanged.connect(self._on_drone_changed)
        self.altitudeSpin.valueChanged.connect(self._on_param_changed)
        self.gsdSpin.valueChanged.connect(self._on_gsd_changed)
        self.sideOverlapSpin.valueChanged.connect(self._on_param_changed)
        self.speedSpin.valueChanged.connect(self._on_param_changed)
        self.directionSpin.valueChanged.connect(self._on_param_changed)
        self.crossHatchCheck.toggled.connect(self._on_param_changed)
        self.splitSpin.valueChanged.connect(self._on_split_changed)
        self.layerCombo.currentIndexChanged.connect(self._on_layer_changed)
        self.featureCombo.currentIndexChanged.connect(self._on_feature_changed)
        self.useSelectionBtn.clicked.connect(self._on_use_qgis_selection)
        self.destCombo.currentIndexChanged.connect(self._on_destination_changed)
        self.localBrowseBtn.clicked.connect(self._pick_local_export_folder)
        self.localFolderEdit.textChanged.connect(self._on_local_folder_changed)
        self.rcRefreshBtn.clicked.connect(self._on_refresh_rc_missions)
        self.rcManualBtn.clicked.connect(self._on_locate_folder_manually)
        self.rcMissionCombo.currentIndexChanged.connect(self._on_rc_mission_selected)
        self.splitPartCombo.currentIndexChanged.connect(self._update_export_button)
        self.rcThumbLabel.clicked.connect(self._open_thumbnail_viewer)
        self.drawPolygonBtn.clicked.connect(self._on_draw_polygon)
        self.editPolygonBtn.clicked.connect(self._on_edit_polygon)
        self.removePolygonBtn.clicked.connect(self._on_remove_drawn_polygon)
        self.autoDirectionBtn.clicked.connect(self._on_auto_direction)
        self.previewBtn.clicked.connect(self._on_preview)
        self.clearPreviewBtn.clicked.connect(self._on_clear_preview)
        self.exportBtn.clicked.connect(self._on_export)

    def _on_param_changed(self):
        self._update_gsd()
        self._update_interval()
        self._update_stats()
        # If a preview is already on the map, keep it live-synced to the new
        # parameters instead of clearing it. Does nothing when no preview shown.
        self._sync_preview()

    def _on_split_changed(self):
        # Ignore the change we make ourselves when the default tracks the
        # battery estimate; only a real user edit stops the auto-tracking.
        if self._setting_split:
            return
        self._split_overridden = True
        self._update_stats()
        self._refresh_split_part_combo()
        self._sync_preview()

    # ── Drone / camera ────────────────────────────────────────────────────

    def _on_drone_changed(self):
        self._update_camera_info()
        self._apply_speed_range()
        self._apply_drone_capabilities()
        self._on_param_changed()

    def _apply_drone_capabilities(self):
        """Adapt the panel to the selected drone's category.

        Consumer (DJI Fly) drones support multi-battery splitting and the direct
        RC transfer. Enterprise (DJI Pilot 2) mapping missions are a single
        polygon that Pilot 2 rebuilds, and are imported as a file, so those two
        features are hidden/disabled rather than shown and then rejected."""
        drone = self.droneModelCombo.currentText()
        if not registry.has(drone):
            return
        consumer = registry.get(drone).category == 'consumer'

        # Split Missions row (label + spin): consumer only.
        self.splitSpin.setVisible(consumer)
        lbl = self._organizer_form.labelForField(self.splitSpin)
        if lbl is not None:
            lbl.setVisible(consumer)
        if not consumer:
            self._setting_split = True
            self.splitSpin.blockSignals(True)
            self.splitSpin.setValue(1)
            self.splitSpin.blockSignals(False)
            self._setting_split = False
            self._split_overridden = False

        # Send to DJI RC is a DJI Fly transfer; grey it out for enterprise.
        self._set_destination_rc_enabled(consumer)

        # Rows that depend on the mission type (full vs semi automatic).
        self._apply_mission_type_capabilities()

    # ── Mission type (semi- / full-automatic 2D mapping) ──────────────────

    def _mission_type(self):
        """'full' for full-automatic capture (a waypoint per photo), else 'semi'."""
        return 'full' if self.captureFullRadio.isChecked() else 'semi'

    def _on_mission_type_changed(self):
        self._apply_mission_type_capabilities()
        self._on_param_changed()

    @staticmethod
    def _set_row_visible(form, widget, visible):
        """Show/hide a QFormLayout row (its field widget and its label)."""
        widget.setVisible(visible)
        lbl = form.labelForField(widget)
        if lbl is not None:
            lbl.setVisible(visible)

    def _apply_mission_type_capabilities(self):
        """Swap the panel between semi- and full-automatic layouts.

        The Front Overlap row (under Side Overlap in Flight Parameters) shows a
        derived read-out in semi-automatic and a direct input in full-automatic.
        Max Waypoints applies to both modes, so it stays visible."""
        full = self._mission_type() == 'full'
        self.frontOverlapStack.setCurrentIndex(1 if full else 0)

    def _set_destination_rc_enabled(self, enabled):
        """Enable/disable the 'Send to DJI RC' destination item (kept visible,
        greyed when disabled). Falls back to 'Save to computer' if RC was active."""
        idx = self.destCombo.findData('rc')
        if idx < 0:
            return
        item = self.destCombo.model().item(idx)
        if item is not None:
            item.setEnabled(enabled)
        if not enabled and self.destCombo.currentData() == 'rc':
            local_idx = self.destCombo.findData('local')
            if local_idx >= 0:
                self.destCombo.setCurrentIndex(local_idx)

    def _update_camera_info(self):
        drone = self.droneModelCombo.currentText()
        self.cameraInfoLabel.setText(
            registry.get(drone).info if registry.has(drone) else '—'
        )

    def _apply_speed_range(self):
        """Set the Speed control's limits to the selected drone's own range.

        The min/max come from hardware/drones.json, so adding a drone with a
        different speed envelope needs no code change. Signals are blocked while
        the range moves; the caller recomputes stats afterwards (setRange clamps
        the current value into the new range automatically)."""
        drone = self.droneModelCombo.currentText()
        if not registry.has(drone):
            return
        lo, hi = registry.get(drone).speed_range()
        self.speedSpin.blockSignals(True)
        self.speedSpin.setRange(lo, hi)
        self.speedSpin.blockSignals(False)

    # ── GSD ───────────────────────────────────────────────────────────────

    def _calc_gsd(self):
        drone = self.droneModelCombo.currentText()
        if not registry.has(drone):
            return None
        return round(
            registry.get(drone).camera.gsd_cm_per_px(self.altitudeSpin.value()), 2
        )

    def _calc_altitude_from_gsd(self):
        """Inverse of _calc_gsd: altitude (m) needed to reach the current GSD."""
        drone = self.droneModelCombo.currentText()
        if not registry.has(drone):
            return None
        cam = registry.get(drone).camera
        gsd = self.gsdSpin.value()
        if gsd <= 0:
            return None
        return (gsd * cam.focal_length_mm * cam.image_width_px) / (cam.sensor_width_mm * 100.0)

    def _update_gsd(self):
        """Refresh the GSD field from the current altitude (altitude drives GSD)."""
        gsd = self._calc_gsd()
        if gsd is None:
            return
        self.gsdSpin.blockSignals(True)
        self.gsdSpin.setValue(gsd)
        self.gsdSpin.blockSignals(False)

    def _on_gsd_changed(self):
        """User edited GSD: set altitude accordingly, clamped to its range."""
        alt = self._calc_altitude_from_gsd()
        if alt is None:
            return
        alt = max(self.altitudeSpin.minimum(), min(self.altitudeSpin.maximum(), alt))
        self.altitudeSpin.blockSignals(True)
        self.altitudeSpin.setValue(alt)
        self.altitudeSpin.blockSignals(False)
        # Reflect the clamped/rounded altitude back into GSD and refresh everything
        # downstream (footprint, interval, stats, preview) via the shared handler.
        self._on_param_changed()

    # ── Footprint & trigger interval ──────────────────────────────────────

    def _footprint(self):
        drone = self.droneModelCombo.currentText()
        if not registry.has(drone):
            return None, None
        cam = registry.get(drone).camera
        alt = self.altitudeSpin.value()
        return cam.footprint_across(alt), cam.footprint_along(alt)

    def _full_auto_spacing(self):
        """Along-track distance between photo waypoints in full-automatic mode:
        footprint_along x (1 - front overlap). None if the drone/altitude is not
        resolved yet."""
        _, footprint_along = self._footprint()
        if not footprint_along:
            return None
        front = self.frontOverlapSpin.value() / 100.0
        return max(footprint_along * (1.0 - front), 0.5)

    def _update_interval(self):
        # Full-automatic: Shot Spacing and the derived Front Overlap are hidden
        # (front overlap is a direct input), so there is nothing to recompute.
        if self._mission_type() == 'full':
            return
        _, fh = self._footprint()
        spd = self.speedSpin.value()
        interval = self.photoIntervalSpin.value()
        if fh is None or spd <= 0 or interval <= 0:
            self.frontOverlapLabel.setText('—')
            self.frontOverlapLabel.setObjectName('frontOverlapLabel')
            self.frontOverlapLabel.style().unpolish(self.frontOverlapLabel)
            self.frontOverlapLabel.style().polish(self.frontOverlapLabel)
            return
        actual_spacing = spd * interval
        overlap_pct = (1.0 - actual_spacing / fh) * 100.0
        # Show the aircraft's minimum shutter interval alongside the value, so
        # the pilot knows the floor the Photo Interval must respect.
        drone = self.droneModelCombo.currentText()
        min_int = (registry.get(drone).camera.min_shoot_interval_s
                   if registry.has(drone) else None)
        tail = f'   ·   min {min_int:g} s' if min_int else ''
        if overlap_pct < 60:
            self.frontOverlapLabel.setText(f'{overlap_pct:.0f} %  — too low{tail}')
            self.frontOverlapLabel.setObjectName('frontOverlapWarnLabel')
        elif overlap_pct < 70:
            self.frontOverlapLabel.setText(f'{overlap_pct:.0f} %  — marginal{tail}')
            self.frontOverlapLabel.setObjectName('frontOverlapWarnLabel')
        else:
            self.frontOverlapLabel.setText(f'{overlap_pct:.0f} %{tail}')
            self.frontOverlapLabel.setObjectName('frontOverlapLabel')
        self.frontOverlapLabel.style().unpolish(self.frontOverlapLabel)
        self.frontOverlapLabel.style().polish(self.frontOverlapLabel)

    # ── QGIS selection sync ───────────────────────────────────────────────

    def _on_use_qgis_selection(self):
        """
        Inspect the current QGIS selection across all non-internal polygon
        layers and adopt it as the FlyPath survey polygon.

        Rules:
          0 selected features total → error
          > 1 selected features total → error
          exactly 1 selected feature  → sync Layer/Feature combos + survey polygon
        """
        candidates = []
        for layer in QgsProject.instance().mapLayers().values():
            if (not hasattr(layer, 'wkbType') or
                    layer.customProperty('flypath_internal') or
                    QgsWkbTypes.geometryType(layer.wkbType()) !=
                    _PolygonGeometry):
                continue
            for fid in layer.selectedFeatureIds():
                candidates.append((layer, fid))

        if len(candidates) == 0:
            QMessageBox.information(
                self, 'Nothing Selected',
                'No polygon is selected in QGIS.\n\n'
                'Select a single polygon with the QGIS selection tool and try again.'
            )
            return

        if len(candidates) > 1:
            QMessageBox.warning(
                self, 'Multiple Polygons Selected',
                f'{len(candidates)} polygons are selected across one or more layers.\n\n'
                'FlyPath supports one survey polygon at a time.\n'
                'Select exactly one polygon and try again.'
            )
            return

        layer, fid = candidates[0]
        feat = next(layer.getFeatures([fid]))

        # Remove any active drawn polygon
        if self._survey_area_layer_id:
            self._on_remove_drawn_polygon()

        # Sync Layer combo
        layer_idx = self.layerCombo.findData(layer.id())
        if layer_idx < 0:
            return
        self.layerCombo.blockSignals(True)
        self.layerCombo.setCurrentIndex(layer_idx)
        self.layerCombo.blockSignals(False)

        # Connect layer edit signals if not already done
        if self._monitored_layer_id != layer.id():
            self._disconnect_layer_signals()
            self._connect_layer_signals(layer)

        # Sync Feature combo for multi-feature layers
        if layer.featureCount() > 1:
            self._populate_feature_combo(layer)
            feat_idx = self.featureCombo.findData(fid)
            if feat_idx >= 0:
                self.featureCombo.blockSignals(True)
                self.featureCombo.setCurrentIndex(feat_idx)
                self.featureCombo.blockSignals(False)
        else:
            self.featureCombo.setVisible(False)

        self._set_survey_polygon(feat.geometry(), layer.crs(),
                                 layer_id=layer.id(), fid=fid)

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

        # A layer-based polygon replaces any drawn polygon
        if self._survey_area_layer_id:
            self._remove_survey_area_layer()
            self._on_clear_preview(reset_area=False)
            self._survey_polygon     = None
            self._survey_polygon_crs = None

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
            except RuntimeError:
                pass  # signals already disconnected — safe to ignore
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
                self._syncing_selection = True
                layer.selectByIds([fid])
                self._syncing_selection = False
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
                'A survey this large needs more than one battery. Use the '
                'Split Missions field in Flight Parameters to divide it into '
                'separate missions, one per battery (it defaults to the '
                'estimated Batteries count), then export and fly each part in '
                'turn.'
            )

    @staticmethod
    def _guess_name_field(layer):
        """Return the first text-like field name, or None."""
        for field in layer.fields():
            if field.type() in (QVariant.String,):
                return field.name()
        return None

    def _on_draw_polygon(self, checked):
        if checked:
            if self._survey_polygon is not None:
                reply = QMessageBox.question(
                    self, 'Replace Survey Area?',
                    'A survey area is already defined.\n\n'
                    'Do you want to discard it and draw a new polygon?',
                    _MB_YES | _MB_NO,
                    _MB_NO,
                )
                if reply != _MB_YES:
                    self.drawPolygonBtn.setChecked(False)
                    return
                # User confirmed — clear everything before drawing
                self._on_clear_preview(reset_area=True)

            # Reset layer / feature selection — drawn polygon is standalone
            self._disconnect_layer_signals()
            self._clear_layer_selection()
            self.layerCombo.blockSignals(True)
            self.layerCombo.setCurrentIndex(0)
            self.layerCombo.blockSignals(False)
            self.featureCombo.blockSignals(True)
            self.featureCombo.clear()
            self.featureCombo.setVisible(False)
            self.featureCombo.blockSignals(False)

            canvas = self.iface.mapCanvas()
            self._prev_map_tool = canvas.mapTool()
            self._draw_tool = PolygonDrawTool(canvas)
            self._draw_tool.polygon_completed.connect(self._on_polygon_drawn)
            self._draw_tool.drawing_cancelled.connect(self._on_drawing_cancelled)
            canvas.setMapTool(self._draw_tool)
            self.drawPolygonBtn.setText('Drawing…  (right-click or double-click to finish)')
        else:
            self._cancel_draw_tool()

    def _leave_draw_tool(self):
        """Turn off the crosshair draw tool. Restores whatever map tool was
        active before drawing, or unsets ours if there was none, so the plus
        cursor is only ever shown while Draw or Edit is active."""
        canvas = self.iface.mapCanvas()
        current = canvas.mapTool()
        if self._prev_map_tool is not None:
            canvas.setMapTool(self._prev_map_tool)
            self._prev_map_tool = None
        elif isinstance(current, PolygonDrawTool):
            # No earlier tool to fall back to (the canvas had none when Draw
            # started); clear ours so the cursor returns to the default arrow.
            canvas.unsetMapTool(current)
        self._draw_tool = None

    def _on_polygon_drawn(self, geom):
        self.drawPolygonBtn.setChecked(False)
        self.drawPolygonBtn.setText('Draw Polygon on Map')
        self._leave_draw_tool()
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
            'color': '255,20,147,85',
            'outline_color': '#FF1493',
            'outline_width': '0.8',
            'outline_style': 'dash',
        })
        layer.renderer().setSymbol(symbol)

        # Track geometry edits made via QGIS tools
        layer.editingStopped.connect(self._on_survey_area_edited)
        layer.geometryChanged.connect(self._on_survey_area_geometry_changed)

        QgsProject.instance().addMapLayer(layer)
        self._survey_area_layer_id = layer.id()
        self.editPolygonBtn.setText('✎ Edit')
        self.editPolygonBtn.setVisible(True)
        self.removePolygonBtn.setVisible(True)
        self.iface.mapCanvas().refresh()

    def _on_survey_area_edited(self):
        """Called after the user commits edits to the survey area layer."""
        self._finish_polygon_edit()
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
        # Guarantee the crosshair is gone: if a draw is somehow still active
        # (e.g. it started with no prior tool to fall back to), drop it here.
        self._leave_draw_tool()
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
                except (TypeError, RuntimeError):
                    # Signals were never connected, or the layer's C++ object
                    # is already gone; nothing to disconnect.
                    pass
                QgsProject.instance().removeMapLayer(self._survey_area_layer_id)
            self._survey_area_layer_id = None
        self.editPolygonBtn.setVisible(False)
        self.editPolygonBtn.setText('✎ Edit')
        self.removePolygonBtn.setVisible(False)

    def _on_edit_polygon(self):
        """Toggle vertex editing of the drawn survey polygon with QGIS tools."""
        if not self._survey_area_layer_id:
            return
        layer = QgsProject.instance().mapLayer(self._survey_area_layer_id)
        if layer is None:
            return
        if layer.isEditable():
            # Finish: commit the edits. This fires editingStopped, which runs
            # _on_survey_area_edited to sync the polygon and reset the UI.
            layer.commitChanges()
            return
        # Start: make the layer active, begin editing, and switch to the QGIS
        # Vertex Tool so the user can drag, add and delete vertices. The
        # existing geometryChanged/editingStopped hooks keep FlyPath in sync.
        self.iface.setActiveLayer(layer)
        layer.startEditing()
        self._prev_tool_before_edit = self.iface.mapCanvas().mapTool()
        try:
            self.iface.actionVertexTool().trigger()
        except AttributeError:
            try:
                self.iface.actionNodeTool().trigger()   # older QGIS name
            except AttributeError:
                pass
        self.editPolygonBtn.setText('✓ Finish Editing')
        self.infoBar.setText(
            'Editing the survey polygon: drag a vertex to move it, click an '
            'edge to add one, or select a vertex and press Delete to remove '
            'it. Click Finish Editing when done.'
        )

    def _finish_polygon_edit(self):
        """Reset the Edit button, restore the previous map tool and info bar."""
        self.editPolygonBtn.setText('✎ Edit')
        tool = getattr(self, '_prev_tool_before_edit', None)
        if tool is not None:
            self.iface.mapCanvas().setMapTool(tool)
            self._prev_tool_before_edit = None
        self.infoBar.setText(_INFO_IDLE)

    def _on_drawing_cancelled(self):
        self.drawPolygonBtn.setChecked(False)
        self.drawPolygonBtn.setText('Draw Polygon on Map')
        self._leave_draw_tool()

    def _cancel_draw_tool(self):
        """Stop drawing, restore the previous map tool, and reset the button."""
        self._leave_draw_tool()
        self.drawPolygonBtn.setChecked(False)
        self.drawPolygonBtn.setText('Draw Polygon on Map')

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

    def _apply_split_default(self, max_split, default_target):
        """Set the split spin's range and, until the user overrides it, its
        default (the battery estimate). Split Missions is a minimum: the
        waypoint cap may produce more missions than this, but never fewer. Runs
        with signals blocked so the programmatic change isn't read as an edit."""
        max_split = max(1, max_split)
        drone = self.droneModelCombo.currentText()
        enterprise = registry.has(drone) and registry.get(drone).category == 'enterprise'
        target = self.splitSpin.value()
        if enterprise:
            target = 1                       # enterprise missions are not split
        elif not self._split_overridden:
            target = default_target
        target = max(1, min(target, max_split))
        self._setting_split = True
        self.splitSpin.blockSignals(True)
        self.splitSpin.setMinimum(1)
        self.splitSpin.setMaximum(max_split)
        self.splitSpin.setValue(target)
        self.splitSpin.blockSignals(False)
        self._setting_split = False

    def _refresh_split_part_combo(self):
        """Show and fill the RC 'Mission part' picker to match the split count.

        The RC replaces one mission slot at a time, so when the survey is split
        the user picks which part to send. Hidden when there is a single part.

        Uses the actual mission count, which in full-auto can exceed the Split
        Missions value when the waypoint cap forces extra missions."""
        n = len(self._live_missions) if self._live_missions else self.splitSpin.value()
        prev = self.splitPartCombo.currentData()
        self.splitPartCombo.blockSignals(True)
        self.splitPartCombo.clear()
        if n > 1:
            for i in range(n):
                self.splitPartCombo.addItem(f'Part {i + 1} of {n}', i)
            idx = self.splitPartCombo.findData(prev)
            self.splitPartCombo.setCurrentIndex(idx if idx >= 0 else 0)
        self.splitPartCombo.blockSignals(False)
        self.splitPartRow.setVisible(n > 1)
        self._update_export_button()

    def _update_stats(self):
        self._live_waypoints = None
        self._live_missions  = None
        if not self._has_survey_area(silent=True):
            self._clear_stats()
            return
        drone = self.droneModelCombo.currentText()
        if not registry.has(drone):
            self._clear_stats()
            return

        d     = registry.get(drone)
        speed = self.speedSpin.value()

        # Coverage area
        webmerc = QgsCoordinateReferenceSystem('EPSG:3857')
        xf = QgsCoordinateTransform(
            self._survey_polygon_crs, webmerc, QgsProject.instance()
        )
        g = QgsGeometry(self._survey_polygon)
        g.transform(xf)
        self.coverageLabel.setText(f'{g.area() / 10_000:.2f} ha')

        # Flight-path stats are taken from the ACTUAL generated waypoints (the
        # same path the preview draws), so distance, lines, photos and time
        # always match the real plan and include every turnaround edge. This
        # runs silently on every parameter change; on any failure the
        # path-dependent stats are blanked.
        full = self._mission_type() == 'full'
        if full:
            densify = self._full_auto_spacing()
            actual_spacing = densify or 0.5
        else:
            densify = None
            actual_spacing = max(speed * self.photoIntervalSpin.value(), 0.5)
        try:
            turn_pts = []                    # endpoints only: for line count + distance
            waypoints = []                   # the plan the drone flies (dense if full)
            for direction in self._grid_directions():
                common = dict(
                    polygon_geom=self._survey_polygon,
                    polygon_crs=self._survey_polygon_crs,
                    altitude_m=self.altitudeSpin.value(),
                    shot_spacing_m=actual_spacing,
                    side_overlap=self.sideOverlapSpin.value() / 100.0,
                    direction_deg=direction,
                    margin_m=self.marginSpin.value(),
                    drone_specs=d.grid_specs(),
                )
                tp, _ = generate_flight_grid(**common)
                turn_pts.extend(tp)
                if full:
                    dp, _ = generate_flight_grid(densify_spacing=densify, **common)
                    waypoints.extend(dp)
            if not full:
                waypoints = turn_pts
        except Exception:
            turn_pts = waypoints = None

        if not waypoints or len(waypoints) < 2:
            for attr in ('flightTimeLabel', 'distanceLabel', 'photosLabel',
                         'linesLabel', 'batteriesLabel'):
                getattr(self, attr).setText('—')
            self._hide_hud()
            return

        self._live_waypoints = waypoints

        dist_m     = self._path_length_m(turn_pts)
        n_lines    = len(turn_pts) // 2
        usable_min = d.battery_time_min * (1.0 - _BATTERY_RESERVE)
        if full:
            # Stop-and-shoot: transit plus a stop per photo (the halt, or the
            # camera's shutter/write time if longer).
            n_photos   = len(waypoints)
            per_photo  = max(_HALT_S, d.camera.min_shoot_interval_s)
            flight_min = (dist_m / speed + n_photos * per_photo) / 60.0 if speed > 0 else 0.0
        else:
            n_photos   = max(0, int(dist_m / actual_spacing))
            flight_min = dist_m / (speed * 60.0) if speed > 0 else 0.0
        batteries  = math.ceil(flight_min / usable_min) if flight_min > 0 else 0

        self.flightTimeLabel.setText(f'{flight_min:.1f} min')
        self.distanceLabel.setText(f'{dist_m / 1000:.2f} km')
        self.photosLabel.setText(f'{n_photos:,}')
        self.linesLabel.setText(str(n_lines))
        self.batteriesLabel.setText(str(batteries))
        self._show_hud()

        # Split Missions is the MINIMUM number of missions, defaulting to (and
        # tracking) the battery estimate until the user sets their own value. In
        # full-auto the waypoint cap can raise the actual count above it, but the
        # spin itself stays the user's minimum. Then split for the preview.
        max_split = max(1, len(waypoints) - 1) if full else n_lines
        self._apply_split_default(max_split, batteries)
        self._live_missions = self._split_missions(waypoints)
        self._refresh_split_part_combo()

    def _split_missions(self, waypoints):
        """Split waypoints for the current mission type, honouring the Max
        Waypoints cap so no exported mission exceeds it. Full-automatic splits by
        waypoint count; semi-automatic splits by whole flight lines."""
        n = self.splitSpin.value()
        maxwp = self.maxWaypointsSpin.value()
        if self._mission_type() == 'full':
            return split_by_waypoint_count(waypoints, n, maxwp)
        # Semi: two waypoints per line, plus a shared seam on every mission after
        # the first, so a mission of L lines holds up to 2L + 1 waypoints. Raise
        # the split count so each mission stays within the cap.
        n_lines = len(waypoints) // 2
        if maxwp >= 3 and n_lines >= 1:
            max_lines = max(1, (maxwp - 1) // 2)
            n = max(n, -(-n_lines // max_lines))       # ceil(n_lines / max_lines)
        return split_waypoints(waypoints, n)

    def _path_length_m(self, waypoints):
        """Ellipsoidal length of the (lon, lat) flight path in metres."""
        da = QgsDistanceArea()
        da.setSourceCrs(QgsCoordinateReferenceSystem('EPSG:4326'),
                        QgsProject.instance().transformContext())
        da.setEllipsoid('WGS84')
        return da.measureLine([QgsPointXY(lon, lat) for lon, lat in waypoints])

    def _clear_stats(self):
        for attr in ('flightTimeLabel', 'distanceLabel', 'photosLabel',
                     'linesLabel', 'batteriesLabel', 'coverageLabel'):
            getattr(self, attr).setText('—')
        self.areaLabel.setText('—')
        self._hide_hud()
        # GSD and Interval depend only on drone + altitude, not on a survey
        # polygon — always recompute them rather than blanking them out.
        self._update_gsd()
        self._update_interval()

    def showEvent(self, event):
        super().showEvent(event)
        # Bring the HUD back when the panel reopens, if there is a live plan.
        if self._hud is not None and self._live_waypoints:
            self._show_hud()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._hide_hud()

    # ── Map preview ───────────────────────────────────────────────────────

    def _on_preview(self):
        if not self._has_survey_area():
            return
        result = self._generate_waypoints()
        if result is None:
            return
        waypoints, shot_spacing_m = result
        missions = self._split_missions(waypoints)
        self._waypoints      = waypoints
        self._shot_spacing_m = shot_spacing_m
        self._missions       = missions
        self._on_clear_preview(reset_area=False)
        line_layer = self._build_path_layer(missions)
        wp_layer   = self._build_waypoints_layer(missions)
        self._preview_layer_ids = [line_layer.id(), wp_layer.id()]
        self.iface.mapCanvas().refresh()

    def _path_renderer(self, n_missions):
        """Rule-based renderer that colours each split mission's line its own
        colour. A single (unsplit) mission keeps the original yellow."""
        root = QgsRuleBasedRenderer.Rule(None)
        for m in range(max(1, n_missions)):
            sym = QgsLineSymbol.createSimple({
                'color': _mission_color(m), 'width': '0.8',
                'capstyle': 'round', 'joinstyle': 'round',
            })
            label = 'Flight path' if n_missions <= 1 else f'Mission {m + 1}'
            root.appendChild(QgsRuleBasedRenderer.Rule(
                sym, filterExp=f'"mission" = {m}', label=label))
        return QgsRuleBasedRenderer(root)

    def _build_path_layer(self, missions):
        """Create and register a LineString layer for the flight path, drawing
        one colour-coded polyline per split mission."""
        layer = QgsVectorLayer(
            'LineString?crs=EPSG:4326&field=id:integer&field=mission:integer',
            'FlyPath — Path', 'memory'
        )
        layer.setCustomProperty('flypath_internal', True)
        layer.setRenderer(self._path_renderer(len(missions)))
        self._populate_path_layer(layer, missions)
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _populate_path_layer(self, layer, missions):
        """(Re)fill the path layer with one polyline per mission, in place.

        Missions are drawn as separate polylines with no line joining one to
        the next: the drone lands and swaps battery between them, so there is
        no flight leg connecting them."""
        dp = layer.dataProvider()
        dp.truncate()
        feats = []
        for m, wps in enumerate(missions):
            if len(wps) < 2:
                continue
            feat = QgsFeature()
            feat.setGeometry(QgsGeometry.fromPolylineXY(
                [QgsPointXY(lon, lat) for lon, lat in wps]
            ))
            feat.setAttributes([m, m])
            feats.append(feat)
        dp.addFeatures(feats)
        layer.triggerRepaint()

    def _build_waypoints_layer(self, missions):
        """Create and register a rule-based Point layer for waypoint markers."""
        layer = QgsVectorLayer(
            'Point?crs=EPSG:4326&field=seq:integer&field=wp_type:string(10)'
            '&field=mission:integer',
            'FlyPath — Waypoints', 'memory'
        )
        layer.setCustomProperty('flypath_internal', True)
        self._populate_waypoints_layer(layer, missions)

        root = QgsRuleBasedRenderer.Rule(None)
        for expr, color, border, size, label in [
            ('"wp_type" = \'start\'', _COLOR_START_MARKER, _COLOR_MID_MARKER, '7.5', 'Start'),
            ('"wp_type" = \'end\'',   _COLOR_END_MARKER,   _COLOR_MID_MARKER, '7.5', 'End'),
            ('"wp_type" = \'mid\'',   _COLOR_MID_MARKER,   '#FFE600',         '4.0', 'Waypoint'),
        ]:
            sym = QgsMarkerSymbol.createSimple({
                'name': 'circle', 'color': color,
                'outline_color': border, 'outline_width': '0.4',
                'size': size,
            })
            root.appendChild(QgsRuleBasedRenderer.Rule(sym, filterExp=expr, label=label))
        layer.setRenderer(QgsRuleBasedRenderer(root))

        lbl = QgsPalLayerSettings()
        lbl.fieldName = 'seq'
        try:
            lbl.placement = Qgis.LabelPlacement.OverPoint
        except AttributeError:
            lbl.placement = getattr(QgsPalLayerSettings, 'OverPoint')
        lbl.priority = 10
        fmt = QgsTextFormat()
        fmt.setFont(QFont('Segoe UI', 7, _FontBold))
        fmt.setColor(QColor('#1E2128'))
        fmt.setSize(7)
        lbl.setFormat(fmt)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(lbl))
        layer.setLabelsEnabled(True)

        QgsProject.instance().addMapLayer(layer)
        return layer

    def _populate_waypoints_layer(self, layer, missions):
        """(Re)fill the waypoint markers, in place. Each mission is numbered
        from 1 on its own and gets its own start and end marker."""
        dp = layer.dataProvider()
        dp.truncate()
        wp_feats = []
        for m, wps in enumerate(missions):
            last_idx = len(wps) - 1
            for i, (lon, lat) in enumerate(wps):
                f = QgsFeature()
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
                if i == 0:
                    wp_type = 'start'
                elif i == last_idx:
                    wp_type = 'end'
                else:
                    wp_type = 'mid'
                f.setAttributes([i + 1, wp_type, m])
                wp_feats.append(f)
        dp.addFeatures(wp_feats)
        layer.triggerRepaint()

    def _sync_preview(self):
        """Keep an already-shown preview in sync with the current parameters.

        Reuses the grid just computed for the statistics, so there is no extra
        generation. Does nothing unless a preview is currently on the map; if
        the new parameters can't produce a valid grid, the stale path is
        dropped quietly (no dialog) rather than left showing something wrong.
        """
        if not self._preview_layer_ids:
            return
        missions = self._live_missions
        flat     = self._live_waypoints
        if not flat or len(flat) < 2 or not missions:
            self._on_clear_preview(reset_area=False)
            return
        self._waypoints      = flat
        self._missions       = missions
        self._shot_spacing_m = max(
            self.speedSpin.value() * self.photoIntervalSpin.value(), 0.5
        )
        self._redraw_preview_layers(missions)

    def _redraw_preview_layers(self, missions):
        """Refresh the existing preview layers in place from new missions."""
        proj = QgsProject.instance()
        line_layer = (proj.mapLayer(self._preview_layer_ids[0])
                      if len(self._preview_layer_ids) >= 1 else None)
        wp_layer   = (proj.mapLayer(self._preview_layer_ids[1])
                      if len(self._preview_layer_ids) >= 2 else None)
        if line_layer is None or wp_layer is None:
            # Layers were removed outside the plugin — rebuild from scratch.
            self._on_clear_preview(reset_area=False)
            line_layer = self._build_path_layer(missions)
            wp_layer   = self._build_waypoints_layer(missions)
            self._preview_layer_ids = [line_layer.id(), wp_layer.id()]
        else:
            # The mission count can change between syncs (the split value or the
            # line count moved), so rebuild the colour rules before refilling.
            line_layer.setRenderer(self._path_renderer(len(missions)))
            self._populate_path_layer(line_layer, missions)
            self._populate_waypoints_layer(wp_layer, missions)
        self.iface.mapCanvas().refresh()

    def _on_clear_preview(self, reset_area=True):
        # Always remove the flight-path preview layers
        for lid in self._preview_layer_ids:
            if QgsProject.instance().mapLayer(lid):
                QgsProject.instance().removeMapLayer(lid)
        self._preview_layer_ids = []

        if reset_area:
            # Full reset — also stop any active draw and remove the boundary
            self._leave_draw_tool()
            self._remove_survey_area_layer()
            self._clear_layer_selection()
            self._survey_polygon     = None
            self._survey_polygon_crs = None
            self._waypoints          = []
            self._shot_spacing_m     = 0.0
            self._missions           = []
            self._live_missions      = None
            self._split_overridden   = False
            self.areaLabel.setText('—')
            self.layerCombo.setCurrentIndex(0)
            self.featureCombo.clear()
            self.featureCombo.setVisible(False)
            self._clear_stats()

        self.iface.mapCanvas().refresh()

    # ── Export ────────────────────────────────────────────────────────────

    # ── Destination selector ──────────────────────────────────────────────

    def _on_destination_changed(self, _=None):
        self.destStack.setCurrentIndex(self.destCombo.currentIndex())
        QSettings('FlyPath', 'FlyPath').setValue(
            'dest_mode', self.destCombo.currentData()
        )
        self._refresh_split_part_combo()
        self._update_export_button()

    def _on_local_folder_changed(self, text):
        QSettings('FlyPath', 'FlyPath').setValue('local_export_dir', text.strip())

    def _pick_local_export_folder(self):
        """Open a standard folder picker and store the chosen folder."""
        start = self.localFolderEdit.text().strip()
        if not (start and os.path.isdir(start)):
            start = os.path.expanduser('~')
        folder = QFileDialog.getExistingDirectory(
            self, 'Choose a folder to save FlyPath missions', start
        )
        if folder:
            self.localFolderEdit.setText(os.path.normpath(folder))

    def _update_export_button(self, _=None):
        """Make the Export button say exactly what it will do."""
        if self.destCombo.currentData() == 'rc':
            mission = self.rcMissionCombo.currentData()
            if mission:
                part = ''
                if self.splitSpin.value() > 1 and self.splitPartRow.isVisible():
                    part = f' with {self.splitPartCombo.currentText()}'
                self.exportBtn.setText(
                    f'Replace "{self._mission_display(mission)}"{part} on RC'
                )
            else:
                self.exportBtn.setText('Send to DJI RC')
        else:
            self.exportBtn.setText('Export KMZ')

    # ── RC mission picker ──────────────────────────────────────────────────

    # DJI mission UUID folder format: 8-4-4-4-12 hex characters
    _UUID_RE = re.compile(
        r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}'
        r'-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    )

    @staticmethod
    def _mission_display(mission):
        """Short label for the Export button: the mission date."""
        return mission.get('date_str') or mission['uuid']

    @staticmethod
    def _mission_label(mission):
        """Full label for the mission dropdown: date + waypoint count."""
        return f'{mission.get("date_str")}  ·  {mission["n_wp"]} wp'

    def _populate_mission_combo(self, missions):
        self.rcMissionCombo.blockSignals(True)
        self.rcMissionCombo.clear()
        for m in (missions or []):
            self.rcMissionCombo.addItem(self._mission_label(m), m)
        self.rcMissionCombo.blockSignals(False)
        # Signals were blocked, so drive the selection-dependent UI directly.
        self._update_export_button()
        self._load_selected_thumbnail()

    def _on_rc_mission_selected(self, _=None):
        self._update_export_button()
        self._load_selected_thumbnail()

    # ── Mission thumbnail preview ──────────────────────────────────────────

    def _thumb_cache_dir(self):
        if not self._thumb_dir:
            self._thumb_dir = tempfile.mkdtemp(prefix='flypath_thumbs_')
        return self._thumb_dir

    def _load_selected_thumbnail(self):
        """Draw a preview of the selected RC mission from its waypoints."""
        self._current_thumb_full = None
        mission = self.rcMissionCombo.currentData()
        if not mission:
            self.rcThumbLabel.setPixmap(QPixmap())
            self.rcThumbLabel.setText('')
            return
        path = self._mission_preview_path(mission)
        self._current_thumb_full = path
        pix = QPixmap(path) if path else QPixmap()
        if not pix.isNull():
            self.rcThumbLabel.setText('')
            self.rcThumbLabel.setPixmap(
                pix.scaled(QSize(240, 135), _KeepAspect, _SmoothTrans)
            )
        else:
            self.rcThumbLabel.setPixmap(QPixmap())
            self.rcThumbLabel.setText('No waypoints to preview')

    def _mission_preview_path(self, mission):
        """Rendered preview of the mission's waypoints, cached per mission UUID."""
        uuid = mission['uuid']
        cached = self._render_cache.get(uuid)
        if cached and os.path.isfile(cached):
            return cached
        path = self._render_mission_preview(mission.get('waypoints') or [], uuid)
        if path:
            self._render_cache[uuid] = path
        return path

    def _render_mission_preview(self, waypoints, uuid):
        """
        Draw the mission's flight path from its waypoints. Rendered from the KMZ
        (the source of truth), so it always matches what is on the RC — unlike
        DJI's static thumbnail, which is not regenerated after a replace.
        Returns a local image path, or None when there are no waypoints.
        """
        if not waypoints:
            return None
        W, H, pad = 1200, 675, 48
        pix = QPixmap(W, H)
        pix.fill(QColor('#EDEAE2'))
        painter = QPainter(pix)
        try:
            painter.setRenderHint(_Antialias, True)
            lons = [c[0] for c in waypoints]
            lats = [c[1] for c in waypoints]
            minx, maxx = min(lons), max(lons)
            miny, maxy = min(lats), max(lats)
            dx = (maxx - minx) or 1e-9
            dy = (maxy - miny) or 1e-9
            s  = min((W - 2 * pad) / dx, (H - 2 * pad) / dy)
            ox = (W - s * dx) / 2.0
            oy = (H - s * dy) / 2.0

            def to_px(lon, lat):
                return QPointF(ox + (lon - minx) * s,
                               H - (oy + (lat - miny) * s))   # flip Y

            pts = [to_px(lon, lat) for lon, lat in waypoints]
            pen = QPen(QColor('#2ECC71'))
            pen.setWidth(4)
            painter.setPen(pen)
            painter.drawPolyline(QPolygonF(pts))
            painter.setPen(QColor('#25A05A'))
            painter.setBrush(QColor('#2ECC71'))
            for pt in pts:
                painter.drawEllipse(pt, 5, 5)
            painter.setBrush(QColor(_COLOR_START_MARKER))
            painter.drawEllipse(pts[0], 8, 8)
            painter.setBrush(QColor(_COLOR_END_MARKER))
            painter.drawEllipse(pts[-1], 8, 8)
        finally:
            painter.end()

        path = os.path.join(self._thumb_cache_dir(), uuid + '_wp.jpg')
        pix.save(path, 'JPG', 90)
        return path

    def _open_thumbnail_viewer(self):
        """Open the full-size, zoomable/pannable view of the selected mission."""
        if not (self._current_thumb_full and os.path.isfile(self._current_thumb_full)):
            return
        mission = self.rcMissionCombo.currentData()
        title = 'Mission preview'
        if mission:
            title = 'Mission  ' + (mission.get('date_str') or mission['uuid'])
        _ThumbnailViewer(self._current_thumb_full, title, self).exec()

    def _set_rc_target(self, path):
        """Record the chosen waypoint folder and show it in the panel."""
        self._rc_waypoint_path = path
        self.rcTargetEdit.setText(path or '')

    def _warn_no_missions(self):
        """Guidance shown when a waypoint folder is found but holds no missions."""
        QMessageBox.warning(
            self, 'No Mission to Replace',
            'The waypoint folder was found, but it has no waypoint mission for '
            'FlyPath to replace.\n\n'
            'FlyPath can only replace a mission that already exists. To create '
            'one:\n'
            '  1. On the RC, open DJI Fly.\n'
            '  2. Make a waypoint mission (even a 3-point dummy will do) and '
            'save it.\n'
            '  3. Back here, press Auto Detect RC (or Locate folder manually) '
            'again.'
        )

    @staticmethod
    def _find_waypoint_on_drives():
        """
        Look for the DJI waypoint folder on a lettered drive (SD card, mapped
        or removable drive). Returns the waypoint folder path, or None.

        This makes auto-detect work regardless of which letter the drive gets:
        it checks every present fixed/removable drive for the fixed DJI path
        rather than assuming a specific letter.
        """
        rel = os.path.join(*_RC_REL_PARTS)
        roots = []
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            bitmask = k32.GetLogicalDrives()
            for i in range(26):
                if not (bitmask >> i) & 1:
                    continue
                root = chr(ord('A') + i) + ':\\'
                # 2 = removable, 3 = fixed; skip optical/network/etc. to avoid
                # slow probes or "insert disk" prompts.
                if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) in (2, 3):
                    roots.append(root)
        except Exception:
            import string
            roots = [c + ':\\' for c in string.ascii_uppercase
                     if os.path.isdir(c + ':\\')]
        for root in roots:
            candidate = os.path.join(root, rel)
            try:
                if os.path.isdir(candidate):
                    return candidate
            except (OSError, ValueError):
                continue
        return None

    def _on_refresh_rc_missions(self):
        """Auto-detect the RC (removable drive or USB/MTP) and list missions."""
        QApplication.setOverrideCursor(_WaitCursor)
        self.rcStatusLabel.setText('Scanning for the RC…')
        QApplication.processEvents()
        drive_path = None
        try:
            # 1) Fast: a lettered/removable drive holding the DJI waypoint path.
            drive_path = self._find_waypoint_on_drives()
            if drive_path:
                status, missions = self._list_missions_from_dir(drive_path)
                wp_path, detail = drive_path, ''
            else:
                # 2) The usual case: an MTP device connected over USB.
                status, wp_path, missions, detail = self._list_rc_missions()
        finally:
            QApplication.restoreOverrideCursor()

        if status == 'ok':
            self._set_rc_target(wp_path)
            self._populate_mission_combo(missions)
            where = 'drive' if drive_path else 'USB'
            self.rcStatusLabel.setText(
                f'DJI RC found ({where}) · {len(missions)} mission(s)'
            )
            return

        self._set_rc_target(wp_path if status == 'no_mission' else None)
        self._populate_mission_combo([])
        if status == 'no_mission':
            self.rcStatusLabel.setText('RC found · no missions to replace')
            self._warn_no_missions()
        elif status == 'not_connected':
            self.rcStatusLabel.setText(
                'No DJI RC detected — connect via USB, or Locate folder manually'
            )
            QMessageBox.information(
                self, 'No DJI RC Detected',
                'No DJI Remote Controller was found.\n\n'
                'FlyPath checked both your drives and USB devices.\n\n'
                'Connect the RC via USB and enable file transfer on it, then '
                'press Auto Detect RC. If it still is not found, use '
                '"Locate folder manually" to point FlyPath at the waypoint '
                'folder yourself.'
            )
        else:
            self.rcStatusLabel.setText('Could not read the RC')
            if detail:
                QMessageBox.warning(self, 'Could Not Read RC', detail)

    def _on_locate_folder_manually(self):
        """
        Manual fallback: browse the Windows shell namespace (This PC, including
        the MTP RC and any drives) and pick the waypoint folder yourself.
        """
        dlg = _RcFolderBrowser(self._list_shell_children, self)
        if not dlg.exec():
            return
        parts = dlg.selected_parts()
        if not parts:
            return

        QApplication.setOverrideCursor(_WaitCursor)
        try:
            status, missions = self._list_missions_at_path(parts)
        finally:
            QApplication.restoreOverrideCursor()

        display = '\\'.join(parts)
        if status == 'ok':
            self._set_rc_target(display)
            self._populate_mission_combo(missions)
            self.rcStatusLabel.setText(f'Folder · {len(missions)} mission(s)')
        elif status == 'no_mission':
            self._set_rc_target(display)
            self._populate_mission_combo([])
            self.rcStatusLabel.setText('Folder selected · no missions found')
            self._warn_no_missions()
        else:
            self._set_rc_target(None)
            self._populate_mission_combo([])
            self.rcStatusLabel.setText('That folder has no missions')
            QMessageBox.warning(
                self, 'No Missions Found',
                'No DJI waypoint missions were found in that folder.\n\n'
                'Pick the "waypoint" folder itself (the one that holds the '
                'mission UUID folders).'
            )

    def _list_shell_children(self, parts):
        """Return the child folder names of a shell path (parts from This PC)."""
        ps_exe = os.path.join(
            os.environ.get('SystemRoot', r'C:\Windows'),
            r'System32\WindowsPowerShell\v1.0\powershell.exe'
        )
        tmp_dir = tempfile.mkdtemp(prefix='flypath_')
        try:
            sp = os.path.join(tmp_dir, 'children.ps1')
            with open(sp, 'w', encoding='utf-8') as fh:
                fh.write(self._shell_children_script(parts))
            try:
                r = subprocess.run(  # nosec B603
                    [ps_exe, '-NoProfile', '-NonInteractive', '-STA',
                     '-ExecutionPolicy', 'Bypass', '-File', sp],
                    capture_output=True, text=True, timeout=40,
                    creationflags=_NO_WINDOW, startupinfo=_STARTUPINFO
                )
            except Exception:
                return []
            return [ln[2:] for ln in r.stdout.splitlines() if ln.startswith('D|')]
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _shell_children_script(parts):
        """PowerShell: list immediate child folders of a shell path (This PC root)."""
        arr = ', '.join("'" + p.replace("'", "''") + "'" for p in parts)
        return (
            "$ErrorActionPreference = 'SilentlyContinue'\n"
            '$shell = New-Object -ComObject Shell.Application\n'
            "$folder = $shell.Namespace('::{20D04FE0-3AEA-1069-A2D8-08002B30309D}')\n"
            '$parts = @(' + arr + ')\n'
            'foreach ($p in $parts) {\n'
            '    $hit = $null\n'
            '    foreach ($i in $folder.Items()) { if ($i.Name -eq $p) { $hit = $i; break } }\n'
            '    if (-not $hit) { exit 1 }\n'
            '    $folder = $hit.GetFolder\n'
            '    if (-not $folder) { exit 1 }\n'
            '}\n'
            'foreach ($i in $folder.Items()) {\n'
            '    if ($i.IsFolder) { Write-Output ("D|" + $i.Name) }\n'
            '}\n'
        )

    def _list_missions_at_path(self, parts):
        """
        Navigate a chosen shell path and list its waypoint missions.
        Returns (status, missions) with status 'ok' / 'no_mission' / 'error'.
        """
        ps_exe = os.path.join(
            os.environ.get('SystemRoot', r'C:\Windows'),
            r'System32\WindowsPowerShell\v1.0\powershell.exe'
        )
        tmp_dir = tempfile.mkdtemp(prefix='flypath_')
        try:
            sp = os.path.join(tmp_dir, 'list_at.ps1')
            with open(sp, 'w', encoding='utf-8') as fh:
                fh.write(self._missions_at_path_script(parts, tmp_dir))
            try:
                r = subprocess.run(  # nosec B603
                    [ps_exe, '-NoProfile', '-NonInteractive', '-STA',
                     '-ExecutionPolicy', 'Bypass', '-File', sp],
                    capture_output=True, text=True, timeout=120,
                    creationflags=_NO_WINDOW, startupinfo=_STARTUPINFO
                )
            except Exception:
                return ('error', [])
            if r.returncode != 0:
                return ('error', [])
            uuids = [ln[len('UUID='):].strip()
                     for ln in r.stdout.splitlines() if ln.startswith('UUID=')]
            missions = []
            for u in uuids:
                create_ms, n_wp, wpts = self._read_kmz_meta(
                    os.path.join(tmp_dir, u + '.kmz'))
                missions.append({
                    'uuid': u, 'create_ms': create_ms,
                    'date_str': self._fmt_ms(create_ms), 'n_wp': n_wp,
                    'waypoints': wpts,
                })
            missions.sort(key=lambda m: m['create_ms'] or 0, reverse=True)
            return ('ok' if missions else 'no_mission', missions)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _missions_at_path_script(parts, tmp_dir):
        """PowerShell: navigate to a chosen folder, list missions, copy KMZs to tmp_dir."""
        arr = ', '.join("'" + p.replace("'", "''") + "'" for p in parts)
        dest = tmp_dir.replace("'", "''")
        return (
            "$ErrorActionPreference = 'SilentlyContinue'\n"
            '$shell = New-Object -ComObject Shell.Application\n'
            "$folder = $shell.Namespace('::{20D04FE0-3AEA-1069-A2D8-08002B30309D}')\n"
            '$uuidPattern = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
            '[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"\n'
            "$dest = $shell.Namespace('" + dest + "')\n"
            '$parts = @(' + arr + ')\n'
            'foreach ($p in $parts) {\n'
            '    $hit = $null\n'
            '    foreach ($i in $folder.Items()) { if ($i.Name -eq $p) { $hit = $i; break } }\n'
            '    if (-not $hit) { exit 1 }\n'
            '    $folder = $hit.GetFolder\n'
            '    if (-not $folder) { exit 1 }\n'
            '}\n'
            '$wp = $folder\n'
            '$preview = @{}; $hasPreview = $false\n'
            'foreach ($c in $wp.Items()) {\n'
            "    if ($c.IsFolder -and $c.Name -eq 'map_preview') {\n"
            '        $mpf = $c.GetFolder\n'
            '        if ($mpf) { $hasPreview = $true; foreach ($pv in $mpf.Items()) { if ($pv.IsFolder) { $preview[$pv.Name] = $true } } }\n'
            '    }\n'
            '}\n'
            'foreach ($item in $wp.Items()) {\n'
            '    if ($item.IsFolder -and $item.Name -match $uuidPattern) {\n'
            '        if ($hasPreview -and -not $preview.ContainsKey($item.Name)) { continue }\n'
            "        Write-Output ('UUID=' + $item.Name)\n"
            '        $mf = $item.GetFolder\n'
            '        foreach ($f in $mf.Items()) {\n'
            '            if (-not $f.IsFolder) { $dest.CopyHere($f, 0x10); Start-Sleep -Milliseconds 1500 }\n'
            '        }\n'
            '    }\n'
            '}\n'
            'exit 0\n'
        )

    def _list_missions_from_dir(self, wp_dir):
        """
        Scan a real filesystem waypoint folder (SD card, mapped drive, copy).

        Returns (status, missions) with status 'ok' / 'no_mission' / 'error'.
        Each mission dict: {uuid, create_ms, date_str, n_wp}
        """
        try:
            if not os.path.isdir(wp_dir):
                return ('error', [])
            mp_dir  = os.path.join(wp_dir, 'map_preview')
            has_mp  = os.path.isdir(mp_dir)
            preview = set()
            if has_mp:
                preview = {d for d in os.listdir(mp_dir)
                           if os.path.isdir(os.path.join(mp_dir, d))}
            missions = []
            for d in os.listdir(wp_dir):
                full = os.path.join(wp_dir, d)
                if not (os.path.isdir(full) and self._UUID_RE.match(d)):
                    continue
                # Only missions DJI Fly tracks (those with a map_preview entry);
                # if there is no map_preview folder at all, list everything.
                if has_mp and d not in preview:
                    continue
                kmz = os.path.join(full, d + '.kmz')
                create_ms, n_wp, wpts = (self._read_kmz_meta(kmz)
                                         if os.path.exists(kmz) else (0, 0, []))
                missions.append({
                    'uuid': d, 'create_ms': create_ms,
                    'date_str': self._fmt_ms(create_ms), 'n_wp': n_wp,
                    'waypoints': wpts,
                })
            missions.sort(key=lambda m: m['create_ms'] or 0, reverse=True)
            return ('ok' if missions else 'no_mission', missions)
        except Exception:
            return ('error', [])

    def _list_rc_missions(self):
        """
        Scan the connected RC and return all waypoint missions.

        Returns (status, waypoint_path, missions, detail):
          status 'ok'            -> missions is a list of dicts (newest first)
          status 'no_mission'    -> an RC is connected but has no missions
          status 'not_connected' -> no DJI RC detected
          status 'error'         -> scan failed; detail holds the message
        Each mission dict: {uuid, create_ms, date_str, n_wp}
        """
        ps_exe = os.path.join(
            os.environ.get('SystemRoot', r'C:\Windows'),
            r'System32\WindowsPowerShell\v1.0\powershell.exe'
        )
        tmp_dir = tempfile.mkdtemp(prefix='flypath_')
        try:
            list_ps = os.path.join(tmp_dir, 'list_rc.ps1')
            with open(list_ps, 'w', encoding='utf-8') as fh:
                fh.write(self._rc_list_script(tmp_dir))
            try:
                r = subprocess.run(  # nosec B603
                    [ps_exe, '-NoProfile', '-NonInteractive', '-STA',
                     '-ExecutionPolicy', 'Bypass', '-File', list_ps],
                    capture_output=True, text=True, timeout=120,
                    creationflags=_NO_WINDOW, startupinfo=_STARTUPINFO
                )
            except subprocess.TimeoutExpired:
                return ('error', None, [], 'Timed out while reading the RC.')
            except Exception as exc:
                return ('error', None, [], f'PowerShell error: {exc}')

            if r.returncode == _RC_EXIT_DEVICE_NO_WP:
                return ('no_mission', None, [], '')
            if r.returncode == _RC_EXIT_NONE:
                return ('not_connected', None, [], '')
            if r.returncode != _RC_EXIT_FOUND:
                return ('error', None, [],
                        r.stderr.strip() or f'Scan failed (exit {r.returncode}).')

            wp_path = None
            uuids = []
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith('PATH='):
                    wp_path = line[len('PATH='):]
                elif line.startswith('UUID='):
                    uuids.append(line[len('UUID='):])

            missions = []
            for u in uuids:
                kmz = os.path.join(tmp_dir, u + '.kmz')
                create_ms, n_wp, wpts = self._read_kmz_meta(kmz)
                missions.append({
                    'uuid': u,
                    'create_ms': create_ms,
                    'date_str': self._fmt_ms(create_ms),
                    'n_wp': n_wp,
                    'waypoints': wpts,
                })
            missions.sort(key=lambda m: m['create_ms'] or 0, reverse=True)
            if not missions:
                return ('no_mission', wp_path, [], '')
            return ('ok', wp_path, missions, '')
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _rc_list_script(tmp_dir):
        """PowerShell: list every RC mission UUID and copy each KMZ to tmp_dir."""
        rel = ', '.join("'" + p + "'" for p in _RC_REL_PARTS)
        rel_join = '\\'.join(_RC_REL_PARTS)
        dest = tmp_dir.replace("'", "''")   # single-quoted PS string
        return (
            "$ErrorActionPreference = 'SilentlyContinue'\n"
            '$shell = New-Object -ComObject Shell.Application\n'
            "$thisPC = $shell.Namespace('::{20D04FE0-3AEA-1069-A2D8-08002B30309D}')\n"
            '$rel = @(' + rel + ')\n'
            '$uuidPattern = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
            '[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"\n'
            "$dest = $shell.Namespace('" + dest + "')\n"
            '$deviceSeen = $false\n'
            'function Nav($folder, $parts) {\n'
            '    foreach ($part in $parts) {\n'
            '        $hit = $null\n'
            '        foreach ($item in $folder.Items()) {\n'
            '            if ($item.Name -eq $part) { $hit = $item; break }\n'
            '        }\n'
            '        if (-not $hit) { return $null }\n'
            '        $nf = $hit.GetFolder\n'
            '        if (-not $nf) { return $null }\n'
            '        $folder = $nf\n'
            '    }\n'
            '    return $folder\n'
            '}\n'
            'foreach ($device in $thisPC.Items()) {\n'
            '    if (-not $device.IsFolder) { continue }\n'
            "    if ($device.Path -match '^[A-Za-z]:\\\\?$') { continue }\n"
            '    $devFolder = $device.GetFolder\n'
            '    if (-not $devFolder) { continue }\n'
            "    if ($device.Name -match 'DJI|RC') { $deviceSeen = $true }\n"
            '    $roots = New-Object System.Collections.ArrayList\n'
            '    [void]$roots.Add(@($device.Name, $devFolder))\n'
            '    foreach ($vol in $devFolder.Items()) {\n'
            '        if ($vol.IsFolder) {\n'
            '            $vf = $vol.GetFolder\n'
            '            if ($vf) { [void]$roots.Add(@(($device.Name + "\\" + $vol.Name), $vf)) }\n'
            '        }\n'
            '    }\n'
            '    foreach ($root in $roots) {\n'
            '        $wp = Nav $root[1] $rel\n'
            '        if ($wp) {\n'
            '            $deviceSeen = $true\n'
            "            Write-Output ('PATH=' + $root[0] + '\\" + rel_join + "')\n"
            # DJI Fly keeps a map_preview/<UUID> thumbnail folder for every
            # mission it actually tracks. Build that set so we only report
            # missions DJI Fly knows about, not folders pasted in manually.
            '            $preview = @{}\n'
            '            $hasPreviewDir = $false\n'
            '            $mp = $null\n'
            "            foreach ($c in $wp.Items()) { if ($c.IsFolder -and $c.Name -eq 'map_preview') { $mp = $c; break } }\n"
            '            if ($mp) {\n'
            '                $mpf = $mp.GetFolder\n'
            '                if ($mpf) {\n'
            '                    $hasPreviewDir = $true\n'
            '                    foreach ($pv in $mpf.Items()) { if ($pv.IsFolder) { $preview[$pv.Name] = $true } }\n'
            '                }\n'
            '            }\n'
            '            foreach ($item in $wp.Items()) {\n'
            '                if ($item.IsFolder -and $item.Name -match $uuidPattern) {\n'
            # Skip missions with no map_preview entry (not known to DJI Fly).
            # If map_preview is absent entirely, fall back to listing all.
            '                    if ($hasPreviewDir -and -not $preview.ContainsKey($item.Name)) { continue }\n'
            "                    Write-Output ('UUID=' + $item.Name)\n"
            '                    $mf = $item.GetFolder\n'
            '                    foreach ($f in $mf.Items()) {\n'
            '                        if (-not $f.IsFolder) {\n'
            '                            $dest.CopyHere($f, 0x10)\n'
            '                            Start-Sleep -Milliseconds 1500\n'
            '                        }\n'
            '                    }\n'
            '                }\n'
            '            }\n'
            f'            exit {_RC_EXIT_FOUND}\n'
            '        }\n'
            '    }\n'
            '}\n'
            f'if ($deviceSeen) {{ exit {_RC_EXIT_DEVICE_NO_WP} }}\n'
            f'exit {_RC_EXIT_NONE}\n'
        )

    @staticmethod
    def _read_kmz_meta(kmz_path):
        """
        Read (createTime_ms, waypoint_count, waypoints) from a mission KMZ.
        waypoints is a list of (lon, lat) parsed from the wayline Placemarks.
        Returns (0, 0, []) on failure.
        """
        try:
            with zipfile.ZipFile(kmz_path) as z:
                template = z.read('wpmz/template.kml').decode('utf-8', 'replace')
                try:
                    waylines = z.read('wpmz/waylines.wpml').decode('utf-8', 'replace')
                except KeyError:
                    waylines = ''
            m = re.search(r'<wpml:createTime>(\d+)</wpml:createTime>', template)
            create_ms = int(m.group(1)) if m else 0
            n_wp = len(re.findall(r'<wpml:index>', waylines))
            waypoints = []
            for lon, lat in re.findall(
                    r'<coordinates>\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)', waylines):
                try:
                    waypoints.append((float(lon), float(lat)))
                except ValueError:
                    pass
            return create_ms, n_wp, waypoints
        except Exception:
            return 0, 0, []

    @staticmethod
    def _fmt_ms(create_ms):
        """Format a DJI createTime (epoch ms) as the date DJI Fly shows."""
        if not create_ms:
            return 'unknown date'
        try:
            return datetime.datetime.fromtimestamp(
                create_ms / 1000
            ).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return 'unknown date'

    def _open_in_explorer(self, filepath):
        """Open File Explorer with the exported file selected."""
        explorer = os.path.join(
            os.environ.get('SystemRoot', r'C:\Windows'), 'explorer.exe'
        )
        try:
            subprocess.Popen(  # nosec B603
                [explorer, '/select,', os.path.normpath(filepath)]
            )
        except OSError:
            pass

    def _write_mission_kmz(self, filepath, waypoints, mission, create_time_ms=None):
        """Write the KMZ file using current UI parameter values.

        Builds the full MissionSpec; the consumer writer ignores the enterprise
        fields (polygon, overlaps, direction, margin) and vice versa."""
        drone = registry.get(self.droneModelCombo.currentText())
        spec = MissionSpec(
            waypoints=waypoints,
            altitude_m=self.altitudeSpin.value(),
            speed_ms=self.speedSpin.value(),
            finish_action=self.finishActionCombo.currentText(),
            rc_lost_action=self.rcLostActionCombo.currentText(),
            gimbal_pitch=self.gimbalAngleSpin.value(),
            mission_name=mission,
            create_time_ms=create_time_ms,
            polygon=self._survey_polygon_wgs84(),
            side_overlap=self.sideOverlapSpin.value() / 100.0,
            front_overlap=self._front_overlap_fraction(),
            direction_deg=self.directionSpin.value(),
            margin_m=self.marginSpin.value(),
            capture_mode=self._mission_type(),
        )
        write_mission(drone, spec, filepath)

    def _survey_polygon_wgs84(self):
        """Survey boundary as [(lon, lat), ...] in WGS84, unclosed (DJI drops the
        repeated first vertex). None if no polygon is defined."""
        if self._survey_polygon is None or self._survey_polygon_crs is None:
            return None
        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        xform = QgsCoordinateTransform(self._survey_polygon_crs, wgs84,
                                       QgsProject.instance())
        g = QgsGeometry(self._survey_polygon)
        g.transform(xform)
        ring = g.asPolygon()[0] if g.asPolygon() else (
            g.asMultiPolygon()[0][0] if g.asMultiPolygon() else None)
        if not ring:
            return None
        coords = [(pt.x(), pt.y()) for pt in ring]
        if len(coords) > 1 and coords[0] == coords[-1]:
            coords = coords[:-1]
        return coords

    def _front_overlap_fraction(self):
        """Current along-track (front) overlap as a fraction 0..0.99.

        Full-automatic: the user sets it directly. Semi-automatic: it is derived
        from speed x interval versus the along-track footprint."""
        if self._mission_type() == 'full':
            return max(0.0, min(0.99, self.frontOverlapSpin.value() / 100.0))
        _, footprint_along = self._footprint()
        spd = self.speedSpin.value()
        interval = self.photoIntervalSpin.value()
        if not footprint_along or spd <= 0 or interval <= 0:
            return 0.7
        frac = 1.0 - (spd * interval) / footprint_along
        return max(0.0, min(0.99, frac))

    @staticmethod
    def _default_kmz_name():
        """A dated default filename so saved missions don't overwrite each other."""
        return 'FlyPath_' + datetime.datetime.now().strftime('%Y%m%d_%H%M') + '.kmz'

    def _on_export(self):
        if not self._has_survey_area():
            return
        mission = 'FlyPath Mission'

        # ── Resolve waypoints first (needed for both destinations) ────────
        if self._waypoints and self._shot_spacing_m:
            waypoints      = self._waypoints
            shot_spacing_m = self._shot_spacing_m
        else:
            result = self._generate_waypoints()
            if result is None:
                return
            waypoints, shot_spacing_m = result

        drone = registry.get(self.droneModelCombo.currentText())

        # Enterprise (DJI Pilot 2) missions are polygon-based mapping2d, exported
        # as a single file and imported in Pilot 2. Splitting and the DJI Fly RC
        # transfer do not apply to them.
        if drone.category == 'enterprise':
            if self.destCombo.currentData() == 'rc':
                QMessageBox.information(
                    self, 'Enterprise Mission',
                    'Send to DJI RC is for DJI Fly consumer missions. A '
                    f'{drone.name} mission exports as a KMZ file: save it, then '
                    'import it in DJI Pilot 2 on the controller.'
                )
                return
            self._export_local_enterprise(mission, waypoints)
            return

        missions = self._split_missions(waypoints)

        if self.destCombo.currentData() == 'rc':
            # The RC replaces one mission slot, so send just the chosen part.
            if len(missions) > 1:
                part = self.splitPartCombo.currentData()
                if part is None:
                    part = 0
                part_wps = missions[part]
                part_name = f'{mission} {part + 1} of {len(missions)}'
                self._export_rc(part_name, part_wps, shot_spacing_m)
            else:
                self._export_rc(mission, waypoints, shot_spacing_m)
        else:
            self._export_local(mission, missions)

    def _export_local_enterprise(self, mission, waypoints):
        """Save a single enterprise (DJI Pilot 2) mapping2d KMZ from the survey
        polygon. No split: Pilot 2 rebuilds the grid from the polygon."""
        folder = self.localFolderEdit.text().strip() or os.path.expanduser('~')
        if not os.path.isdir(folder):
            reply = QMessageBox.question(
                self, 'Create Folder?',
                f'The folder does not exist:\n{folder}\n\nCreate it?',
                _MB_YES | _MB_NO, _MB_YES,
            )
            if reply != _MB_YES:
                return
            try:
                os.makedirs(folder, exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(self, 'Cannot Create Folder', str(exc))
                return

        filepath, _ = QFileDialog.getSaveFileName(
            self, 'Export Mission KMZ',
            os.path.join(folder, self._default_kmz_name()),
            'DJI Mission File (*.kmz)'
        )
        if not filepath:
            return

        try:
            self._write_mission_kmz(filepath, waypoints, mission)
        except Exception as exc:
            QMessageBox.critical(self, 'Export Failed', str(exc))
            return

        QSettings('FlyPath', 'FlyPath').setValue(
            'local_export_dir', os.path.dirname(filepath)
        )
        reply = QMessageBox.information(
            self, 'Export Complete',
            'Enterprise mapping mission saved.\n\n'
            f'Waypoints: {len(waypoints):,}\n\nSaved to:\n{filepath}\n\n'
            'Import it in DJI Pilot 2 on the controller. Open the folder?',
            _MB_YES | _MB_NO, _MB_YES,
        )
        if reply == _MB_YES:
            self._open_in_explorer(filepath)

    def _export_local(self, mission, missions):
        """Save the mission (or each split mission) as a .kmz in the folder."""
        folder = self.localFolderEdit.text().strip() or os.path.expanduser('~')
        if not os.path.isdir(folder):
            reply = QMessageBox.question(
                self, 'Create Folder?',
                f'The folder does not exist:\n{folder}\n\nCreate it?',
                _MB_YES | _MB_NO, _MB_YES,
            )
            if reply != _MB_YES:
                return
            try:
                os.makedirs(folder, exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(self, 'Cannot Create Folder', str(exc))
                return

        filepath, _ = QFileDialog.getSaveFileName(
            self, 'Export Mission KMZ',
            os.path.join(folder, self._default_kmz_name()),
            'DJI Mission File (*.kmz)'
        )
        if not filepath:
            return

        n = len(missions)
        base, ext = os.path.splitext(filepath)
        ext = ext or '.kmz'
        # One file when unsplit; otherwise name them <base>_1_of_N … so they
        # stay in order and never overwrite one another.
        if n <= 1:
            targets = [(filepath, missions[0], mission)]
        else:
            width = len(str(n))
            targets = []
            for i, wps in enumerate(missions, start=1):
                path = f'{base}_{str(i).zfill(width)}_of_{n}{ext}'
                targets.append((path, wps, f'{mission} {i} of {n}'))

        written = []
        try:
            for path, wps, name in targets:
                self._write_mission_kmz(path, wps, name)
                written.append(path)
        except Exception as exc:
            QMessageBox.critical(self, 'Export Failed', str(exc))
            return

        QSettings('FlyPath', 'FlyPath').setValue(
            'local_export_dir', os.path.dirname(filepath)
        )
        total_wp = sum(len(wps) for _, wps, _ in targets)
        if n <= 1:
            saved_text = f'Saved to:\n{written[0]}'
        else:
            names = '\n'.join('  ' + os.path.basename(p) for p in written)
            saved_text = f'Saved {n} missions to:\n{folder}\n\n{names}'
        if self._mission_type() == 'full':
            capture_text = 'Capture: automatic (photo per waypoint)'
        else:
            capture_text = f'Interval: {self.photoIntervalSpin.value():.1f} s'
        reply = QMessageBox.information(
            self, 'Export Complete',
            f'Missions: {n}  ·  Waypoints: {total_wp:,}  ·  {capture_text}\n\n'
            f'{saved_text}\n\nOpen the folder?',
            _MB_YES | _MB_NO, _MB_YES,
        )
        if reply == _MB_YES:
            self._open_in_explorer(written[0])

    def _export_rc(self, mission, waypoints, shot_spacing_m):
        """Replace the selected mission on the connected RC."""
        target = self.rcMissionCombo.currentData()
        if not target or not self._rc_waypoint_path:
            QMessageBox.information(
                self, 'No Mission Selected',
                'Press Auto Detect RC and choose a mission on the RC to '
                'replace.\n\n'
                'If the list is empty, create a waypoint mission in DJI Fly '
                'first, then Auto Detect RC.'
            )
            return

        # No extra confirmation dialog: the mission was already chosen in the
        # picker and the Export button names it ("Replace ... on RC").
        label = self._mission_display(target)
        # Keep the mission's original createTime so its date still matches
        # DJI Fly (DJI keeps the name; only the waypoints change).
        create_ms = target.get('create_ms') or None

        if os.path.isdir(self._rc_waypoint_path):
            # Manually located folder (SD card / mapped drive / local copy):
            # a plain file write, no MTP transfer needed.
            ok, detail = self._export_to_folder_rc(target['uuid'], mission,
                                                   waypoints, create_ms)
        else:
            # Auto-detected MTP device path: copy over USB via Windows Shell.
            QApplication.setOverrideCursor(_WaitCursor)
            self.infoBar.setText('Sending the mission to the RC, please wait…')
            QApplication.processEvents()
            try:
                ok, detail = self._export_to_mtp_rc(
                    self._rc_waypoint_path, mission, waypoints, shot_spacing_m,
                    target_uuid=target['uuid'], create_time_ms=create_ms,
                )
            finally:
                QApplication.restoreOverrideCursor()
                self.infoBar.setText(_INFO_IDLE)

        if ok:
            # The mission now holds our new waypoints (same date). Update the
            # dropdown label and preview immediately, keyed by UUID, so nothing
            # depends on the combo's stored dict or a fresh Auto Detect.
            uuid = target['uuid']
            target['waypoints'] = waypoints
            target['n_wp'] = len(waypoints)
            idx = self.rcMissionCombo.currentIndex()
            if idx >= 0:
                self.rcMissionCombo.setItemText(idx, self._mission_label(target))
            rendered = self._render_mission_preview(waypoints, uuid)
            if rendered:
                self._render_cache[uuid] = rendered
            else:
                self._render_cache.pop(uuid, None)
            self._load_selected_thumbnail()
            self._update_export_button()
            QMessageBox.information(
                self, 'Exported to RC',
                f'Replaced "{label}" on the DJI RC.\n\n'
                f'Waypoints: {len(waypoints):,}\n'
                f'UUID: {detail}\n\n'
                'The preview now shows the mission you just uploaded. '
                'Reopen DJI Fly on the RC to fly it (DJI refreshes its own '
                'thumbnail when you open the mission).'
            )
        else:
            QMessageBox.critical(self, 'RC Export Failed', detail)

    def _export_to_folder_rc(self, uuid, mission, waypoints, create_time_ms=None):
        """Replace a mission inside a real filesystem waypoint folder.

        Returns (success: bool, detail: str) matching _export_to_mtp_rc.
        """
        folder = os.path.join(self._rc_waypoint_path, uuid)
        if not os.path.isdir(folder):
            return False, f'Mission folder not found:\n{folder}'
        kmz = os.path.join(folder, uuid + '.kmz')
        try:
            self._write_mission_kmz(kmz, waypoints, mission, create_time_ms)
        except Exception as exc:
            return False, str(exc)
        return True, uuid

    def _export_to_mtp_rc(self, rc_dir, mission, waypoints, shot_spacing_m,
                          target_uuid=None, create_time_ms=None):
        """
        Export the KMZ directly to a DJI RC connected as an MTP device.

        Shell.Namespace() cannot resolve 'This PC\\...' paths directly.
        Instead we navigate step-by-step from the 'This PC' CLSID using
        GetFolder, which works with MTP virtual filesystem items.

        If target_uuid is given, that mission is replaced directly; otherwise
        the most recently modified mission folder is used.

        Returns (success: bool, detail: str)
          success=True  → detail is the UUID that was replaced
          success=False → detail is a human-readable error message
        """
        ps_exe = os.path.join(
            os.environ.get('SystemRoot', r'C:\Windows'),
            r'System32\WindowsPowerShell\v1.0\powershell.exe'
        )
        rc_norm  = rc_dir.replace('/', '\\')
        if rc_norm.lower().startswith('this pc\\'):
            rc_norm = rc_norm[len('this pc\\'):]
        parts    = [p for p in rc_norm.split('\\') if p]
        ps_parts = ', '.join("'" + p.replace("'", "''") + "'" for p in parts)
        nav      = self._mtp_nav_fragment(ps_parts)

        tmp_dir = tempfile.mkdtemp(prefix='flypath_')
        try:
            if target_uuid:
                # Picker already chose the mission; the copy step verifies it exists.
                uuid_name = target_uuid
            else:
                uuid_name, err = self._mtp_find_uuid(ps_exe, nav, tmp_dir, rc_dir)
                if uuid_name is None:
                    return False, err

            tmp_kmz = os.path.join(tmp_dir, uuid_name + '.kmz')
            try:
                self._write_mission_kmz(tmp_kmz, waypoints, mission, create_time_ms)
            except Exception as exc:
                return False, f'Could not write KMZ: {exc}'

            return self._mtp_copy_kmz(ps_exe, nav, tmp_dir, uuid_name, tmp_kmz)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _mtp_nav_fragment(ps_parts):
        """Return the PowerShell fragment that navigates from 'This PC' to the waypoint folder."""
        return (
            '$shell = New-Object -ComObject Shell.Application\n'
            "$folder = $shell.Namespace('::{20D04FE0-3AEA-1069-A2D8-08002B30309D}')\n"
            '$parts = @(' + ps_parts + ')\n'
            'foreach ($part in $parts) {\n'
            '    $found = $null\n'
            '    foreach ($item in $folder.Items()) {\n'
            '        if ($item.Name -eq $part) { $found = $item; break }\n'
            '    }\n'
            '    if (-not $found) {\n'
            '        foreach ($item in $folder.Items()) {\n'
            '            if ($item.Name -like ("*" + $part + "*")) { $found = $item; break }\n'
            '        }\n'
            '    }\n'
            f'    if (-not $found) {{ Write-Error ("Not found: " + $part); exit {_MTP_EXIT_NAV_FAIL} }}\n'
            '    $next = $found.GetFolder\n'
            f'    if (-not $next) {{ Write-Error ("Cannot open: " + $part); exit {_MTP_EXIT_NAV_FAIL} }}\n'
            '    $folder = $next\n'
            '}\n'
        )

    @staticmethod
    def _mtp_find_uuid(ps_exe, nav, tmp_dir, rc_dir):
        """
        Run Script 1: navigate to waypoint folder and return the latest UUID folder name.

        Returns (uuid_name, None) on success, or (None, error_message) on failure.
        """
        find_ps = os.path.join(tmp_dir, 'find_uuid.ps1')
        with open(find_ps, 'w', encoding='utf-8') as fh:
            fh.write(
                nav +
                '$uuidPattern = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"\n'
                '$latest = $null; $latestDate = [DateTime]::MinValue\n'
                'foreach ($item in $folder.Items()) {\n'
                '    if ($item.IsFolder -and $item.Name -match $uuidPattern -and $item.ModifyDate -gt $latestDate) {\n'
                '        $latestDate = $item.ModifyDate; $latest = $item\n'
                '    }\n'
                '}\n'
                f'if (-not $latest) {{ exit {_MTP_EXIT_NO_UUID} }}\n'
                'Write-Output $latest.Name\n'
            )

        try:
            r = subprocess.run(  # nosec B603
                [ps_exe, '-NoProfile', '-NonInteractive',
                 '-ExecutionPolicy', 'Bypass', '-File', find_ps],
                capture_output=True, text=True, timeout=30,
                creationflags=_NO_WINDOW, startupinfo=_STARTUPINFO
            )
        except subprocess.TimeoutExpired:
            return None, 'Timed out reading the RC waypoint folder.\nCheck the RC is connected via USB.'
        except Exception as exc:
            return None, f'PowerShell error: {exc}'

        if r.returncode == _MTP_EXIT_NAV_FAIL:
            return None, (
                'Could not navigate to the RC waypoint folder.\n\n'
                f'Path used:\n{rc_dir}\n\n'
                'Check that the RC is connected via USB and the path is correct.\n\n'
                f'Details:\n{r.stderr.strip()}'
            )
        if r.returncode == _MTP_EXIT_NO_UUID or not r.stdout.strip():
            return None, (
                'No valid mission folder found on the RC.\n\n'
                'Open DJI Fly on the RC, create a waypoint mission '
                '(even a 3-point dummy), then export again.\n\n'
                'FlyPath only replaces folders with a valid DJI UUID name.'
            )
        return r.stdout.strip().splitlines()[0].strip(), None

    @staticmethod
    def _mtp_copy_kmz(ps_exe, nav, tmp_dir, uuid_name, tmp_kmz):
        """
        Copy the KMZ into the UUID folder on the RC using IFileOperation.

        IFileOperation overwrites silently on MTP devices — no progress window
        and no "replace this file?" prompt — and is synchronous, so it returns
        only once the transfer is complete (a second or two, no fixed sleep).

        Returns (True, uuid_name) on success, or (False, error_message).
        """
        tmp_kmz_ps = tmp_kmz.replace('/', '\\')
        copy_ps = os.path.join(tmp_dir, 'copy_kmz.ps1')
        with open(copy_ps, 'w', encoding='utf-8') as fh:
            fh.write(
                _IFILEOP_PS +
                nav +
                "$uuid = '" + uuid_name.replace("'", "''") + "'\n"
                '$uuidItem = $null\n'
                'foreach ($item in $folder.Items()) {\n'
                '    if ($item.Name -eq $uuid) { $uuidItem = $item; break }\n'
                '}\n'
                f'if (-not $uuidItem) {{ Write-Error "UUID folder not found on RC"; exit {_MTP_EXIT_UUID_MISSING} }}\n'
                "$res = [FlyPathIFO.Op]::CopyTo('" + tmp_kmz_ps.replace("'", "''") +
                "', $uuidItem, ($uuid + '.kmz'))\n"
                'if ($res -eq "OK") { Write-Output "OK" }\n'
                f'else {{ Write-Error $res; exit {_MTP_EXIT_UUID_NO_OPEN} }}\n'
            )

        try:
            r2 = subprocess.run(  # nosec B603
                [ps_exe, '-NoProfile', '-NonInteractive', '-STA',
                 '-ExecutionPolicy', 'Bypass', '-File', copy_ps],
                capture_output=True, text=True, timeout=60,
                creationflags=_NO_WINDOW, startupinfo=_STARTUPINFO
            )
        except subprocess.TimeoutExpired:
            return False, 'Copy to RC timed out. Check the RC is still connected.'
        except Exception as exc:
            return False, f'Copy error: {exc}'

        if r2.returncode == 0 and 'OK' in r2.stdout:
            return True, uuid_name
        return False, (
            f'Copy to RC failed (exit {r2.returncode}).\n'
            f'{r2.stderr.strip() or r2.stdout.strip()}'
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def cleanup(self):
        """Remove all FlyPath-owned QGIS layers. Called on plugin unload."""
        self._disconnect_layer_signals()
        self._remove_survey_area_layer()
        self._on_clear_preview(reset_area=False)
        # Belt-and-suspenders: remove any remaining flypath_internal layers
        # (e.g. if the user moved the panel or state got out of sync)
        to_remove = [
            lid for lid, layer in QgsProject.instance().mapLayers().items()
            if layer.customProperty('flypath_internal')
        ]
        for lid in to_remove:
            QgsProject.instance().removeMapLayer(lid)
        try:
            QgsProject.instance().layersAdded.disconnect(self._refresh_layer_combo)
            QgsProject.instance().layersRemoved.disconnect(self._refresh_layer_combo)
        except (TypeError, RuntimeError):
            pass
        if self._thumb_dir:
            shutil.rmtree(self._thumb_dir, ignore_errors=True)
            self._thumb_dir = None
        # Remove the map-canvas HUD and its resize hook.
        if self._hud is not None:
            try:
                self.iface.mapCanvas().removeEventFilter(self)
            except (TypeError, RuntimeError):
                pass
            self._hud.deleteLater()
            self._hud = None

    def closeEvent(self, event):
        self.cleanup()
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

    def _grid_directions(self):
        """The flight-line direction(s) to fly. Cross-hatch adds a second grid
        at 90° to the first."""
        base = self.directionSpin.value()
        if self.crossHatchCheck.isChecked():
            return [base, (base + 90.0) % 360.0]
        return [base]

    def _generate_waypoints(self):
        """
        Returns (waypoints, shot_spacing_m) or None on failure.
        waypoints is a list of (lon, lat) turn points only.
        """
        drone = self.droneModelCombo.currentText()
        if not registry.has(drone):
            return None
        full = self._mission_type() == 'full'
        if full:
            densify = self._full_auto_spacing()
            shot_spacing_m = densify or 0.5
        else:
            densify = None
            shot_spacing_m = max(
                self.speedSpin.value() * self.photoIntervalSpin.value(), 0.5
            )
        try:
            waypoints = []
            for direction in self._grid_directions():
                wps, shot_spacing_m = generate_flight_grid(
                    polygon_geom=self._survey_polygon,
                    polygon_crs=self._survey_polygon_crs,
                    altitude_m=self.altitudeSpin.value(),
                    shot_spacing_m=shot_spacing_m,
                    side_overlap=self.sideOverlapSpin.value() / 100.0,
                    direction_deg=direction,
                    margin_m=self.marginSpin.value(),
                    drone_specs=registry.get(drone).grid_specs(),
                    densify_spacing=densify,
                )
                waypoints.extend(wps)
        except ValueError as exc:
            QMessageBox.warning(self, 'Cannot Generate Grid', str(exc))
            return None
        return waypoints, shot_spacing_m
