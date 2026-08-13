import os

from qgis.PyQt.QtWidgets import QAction, QDockWidget
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt

from .flypath_dialog import FlyPathDialog

try:
    _DockLeft  = Qt.DockWidgetArea.LeftDockWidgetArea
    _DockRight = Qt.DockWidgetArea.RightDockWidgetArea
except AttributeError:
    # Old PyQt5 without scoped enums; fetch the unscoped names dynamically.
    _DockLeft  = getattr(Qt, 'LeftDockWidgetArea')
    _DockRight = getattr(Qt, 'RightDockWidgetArea')


class FlyPath:

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dock_widget = None
        self.panel = None

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, 'icon.png'))
        self.action = QAction(icon, 'FlyPath', self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip('Open FlyPath mission planner')
        self.action.triggered.connect(self.toggle_panel)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('FlyPath', self.action)

        self.dock_widget = QDockWidget('FlyPath', self.iface.mainWindow())
        self.dock_widget.setObjectName('FlyPathDock')
        self.dock_widget.setAllowedAreas(
            _DockLeft | _DockRight
        )

        self.panel = FlyPathDialog(self.iface, self.dock_widget)
        self.dock_widget.setWidget(self.panel)

        # Lock the dock to the panel's natural width so it can't be dragged
        # wider or narrower. The two-column layout is designed for one width,
        # and stretching it only adds empty space.
        self.dock_widget.setFixedWidth(self.panel.minimumWidth())

        self.iface.mainWindow().addDockWidget(
            _DockRight, self.dock_widget
        )
        self.dock_widget.hide()
        self.dock_widget.visibilityChanged.connect(self.action.setChecked)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu('FlyPath', self.action)
        if self.dock_widget:
            if self.panel:
                self.panel.cleanup()
            self.dock_widget.visibilityChanged.disconnect(self.action.setChecked)
            self.iface.mainWindow().removeDockWidget(self.dock_widget)
            self.dock_widget.setParent(None)
            self.dock_widget = None

    def toggle_panel(self, checked):
        if checked:
            self.dock_widget.show()
            self.dock_widget.raise_()
        else:
            self.dock_widget.hide()
