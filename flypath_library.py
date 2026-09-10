"""Website mission library embedded in the FlyPath dock."""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QMessageBox,
)

from . import flypath_sync

try:
    _UserRole = Qt.ItemDataRole.UserRole
    _PlainText = Qt.TextFormat.PlainText
    _ActionRole = QMessageBox.ButtonRole.ActionRole
    _Cancel = QMessageBox.StandardButton.Cancel
except AttributeError:
    _UserRole = getattr(Qt, 'UserRole')
    _PlainText = getattr(Qt, 'PlainText')
    _ActionRole = getattr(QMessageBox, 'ActionRole')
    _Cancel = getattr(QMessageBox, 'Cancel')


def conflict_choice(parent):
    dialog = QMessageBox(parent)
    dialog.setWindowTitle('Mission changed on FlyPath')
    dialog.setText('This mission changed since you loaded it. Your local edits are still here.')
    reload_button = dialog.addButton('Load latest…', _ActionRole)
    copy_button = dialog.addButton('Save as new…', _ActionRole)
    cancel_button = dialog.addButton(_Cancel)
    dialog.setDefaultButton(cancel_button)
    dialog.setEscapeButton(cancel_button)
    dialog.exec()
    if dialog.clickedButton() is reload_button:
        return 'reload'
    if dialog.clickedButton() is copy_button:
        return 'copy'
    return None


class MissionLibrary(QWidget):
    missionOpened = pyqtSignal()

    def __init__(self, planner, parent=None):
        super().__init__(parent or planner)
        self.planner = planner
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.connect_button = QPushButton('Connect account…')
        self.connect_button.clicked.connect(self.connect_account)
        header.addWidget(self.connect_button)
        self.disconnect_button = QPushButton('Disconnect')
        self.disconnect_button.clicked.connect(self.disconnect_account)
        header.addWidget(self.disconnect_button)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        self.missions = QTreeWidget()
        self.missions.setHeaderLabels(['Mission', 'Updated'])
        self.missions.setRootIsDecorated(False)
        self.missions.setColumnWidth(0, 210)
        self.missions.itemSelectionChanged.connect(self.update_buttons)
        layout.addWidget(self.missions, 1)
        self.link_label = QLabel()
        self.link_label.setTextFormat(_PlainText)
        self.link_label.setWordWrap(True)
        layout.addWidget(self.link_label)
        note = QLabel('Save changes updates the linked mission. Save as new creates a separate mission. '
                      'Newer website edits are checked before updating.')
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.import_button = QPushButton('Open mission')
        self.import_button.clicked.connect(self.import_selected)
        layout.addWidget(self.import_button)
        self.send_button = QPushButton('Save to FlyPath…')
        self.send_button.setToolTip('Preview a mission in the planner before saving a draft.')
        self.send_button.clicked.connect(self.send_current)
        actions.addWidget(self.send_button)
        self.copy_button = QPushButton('Save as new…')
        self.copy_button.clicked.connect(lambda: self.send_current(save_as_new=True))
        actions.addWidget(self.copy_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.update_buttons()

    def update_buttons(self):
        connected = bool(flypath_sync.load_token())
        link = self.planner._current_website_link()
        self.link_label.setText('Editing: %s' % link['name'] if link else 'Current mission is not linked to FlyPath.')
        self.send_button.setText('Save changes' if link else 'Save to FlyPath…')
        self.copy_button.setVisible(bool(link))
        self.connect_button.setVisible(not connected)
        self.disconnect_button.setVisible(connected)
        self.refresh_button.setEnabled(connected)
        self.import_button.setEnabled(connected and self.missions.currentItem() is not None)
        self.send_button.setEnabled(bool(self.planner._preview_layer_ids and
                                         self.planner._missions))
        self.copy_button.setEnabled(self.send_button.isEnabled())
        if not connected:
            self.status.setText('Connect your FlyPath account to browse missions.')

    def connect_account(self):
        if self.planner._web_token():
            self.refresh()

    def disconnect_account(self):
        flypath_sync.save_token('')
        self.planner._website_link = None
        self.missions.clear()
        self.update_buttons()

    def refresh(self):
        self.missions.clear()
        if not flypath_sync.load_token():
            self.update_buttons()
            return
        self.status.setText('Loading missions…')
        missions = self.planner._run_web(
            'FlyPath mission library',
            lambda token: flypath_sync.list_missions(flypath_sync.load_base_url(), token))
        if missions is not None:
            for mission in missions:
                item = QTreeWidgetItem([
                    mission.get('name') or 'Untitled mission',
                    self.planner._web_date(mission.get('updated_at')),
                ])
                item.setData(0, _UserRole, mission.get('id'))
                self.missions.addTopLevelItem(item)
            self.status.setText('%s missions · flypath.io' % len(missions)
                                if missions else 'No missions yet. Save a previewed plan to start.')
        else:
            self.status.setText('Could not load missions. Try Refresh.')
        self.update_buttons()

    def import_selected(self):
        item = self.missions.currentItem()
        if item is not None:
            if self.planner._on_load_from_website(item.data(0, _UserRole)):
                self.missionOpened.emit()

    def send_current(self, save_as_new=False):
        if self.planner._on_send_to_website(save_as_new=save_as_new):
            self.refresh()
