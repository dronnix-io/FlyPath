"""Website mission library embedded in the FlyPath dock."""

from qgis.PyQt.QtCore import Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QFrame, QWidget, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QMessageBox,
)

from . import flypath_sync

try:
    _UserRole = Qt.ItemDataRole.UserRole
    _PlainText = Qt.TextFormat.PlainText
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
    _AlignLeft = Qt.AlignmentFlag.AlignLeft
    _ActionRole = QMessageBox.ButtonRole.ActionRole
    _Cancel = QMessageBox.StandardButton.Cancel
    _Descending = Qt.SortOrder.DescendingOrder
except AttributeError:
    _UserRole = getattr(Qt, 'UserRole')
    _PlainText = getattr(Qt, 'PlainText')
    _AlignCenter = getattr(Qt, 'AlignCenter')
    _AlignLeft = getattr(Qt, 'AlignLeft')
    _ActionRole = getattr(QMessageBox, 'ActionRole')
    _Cancel = getattr(QMessageBox, 'Cancel')
    _Descending = getattr(Qt, 'DescendingOrder')


SIGNUP_URL = flypath_sync.DEFAULT_BASE_URL + '/register/'
# Why there are two tools: what each one is for.
_ROLES = (
    ('QGIS plugin', 'Plans with your GIS data (layers, selections, DEMs) and sends '
                    'missions straight to your DJI RC.'),
    ('FlyPath.io', 'Your missions\' home: they are archived in your account, easy to '
                   'share with other pilots, and you can plan and edit them in any '
                   'browser, without QGIS.'),
)
# What only the website will offer, beyond what an offline plugin can do.
_COMING = ('Coming to FlyPath.io: cloud 2D map and 3D model generation for each '
           'mission, beyond what an offline plugin can do.')
_ROLES_TOGETHER = ('Both use the same planning engine, so a mission opens in either '
                   'one unchanged. Save it here, refine it on the website, then open '
                   'it again here to send it to the RC.')
_SIGNUP_STYLE = """
QFrame#signupCard {
    background-color: #1C2836; border: 1px solid #2F4A6E;
    border-left: 3px solid #3F9BFF; border-radius: 5px;
}
QLabel#signupTitle { color: #FFFFFF; font-size: 13px; font-weight: bold; }
QLabel#signupText { color: #C8D6E8; }
QLabel#signupRole { color: #9CC4F2; font-weight: bold; }
QLabel#signupComing { color: #F5C451; }
QLabel#signupReason { color: #E6EEF8; }
QLabel#signupHint { color: #8FA0B8; font-size: 10px; }
QPushButton#signupButton {
    background-color: #F0A500; color: #1A1A1A; font-weight: bold;
    border: none; border-radius: 4px; padding: 6px 14px;
}
QPushButton#signupButton:hover { background-color: #FFB520; }
"""


# The mission list in the panel's dark style. Set on the list itself, since
# on QGIS 3 the dock-wide stylesheet does not reach its header.
_LIST_STYLE = """
QTreeWidget { background-color: #1A1D23; border: 1px solid #343843; color: #E6EAF0; }
QTreeWidget::item:selected { background-color: #2D6DB5; color: #FFFFFF; }
QHeaderView { background-color: #262A33; border: none; }
QHeaderView::section {
    background-color: #262A33; color: #A8B0BE; font-weight: bold;
    border: none; border-right: 1px solid #343843; border-bottom: 1px solid #343843;
    padding: 4px 6px;
}
"""


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
        layout.addStretch(0)
        header = QHBoxLayout()
        self.status = QLabel()
        self.status.setTextFormat(_PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.sync_notice = QWidget()
        self.sync_notice.setMaximumWidth(480)
        notice_layout = QVBoxLayout(self.sync_notice)
        notice_layout.setSpacing(16)
        for text, style in (
                ('Sync unavailable', 'font-size: 20px; font-weight: bold;'),
                ('A newer version may be available.', 'font-size: 15px;')):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setAlignment(_AlignCenter)
            label.setStyleSheet(style)
            notice_layout.addWidget(label)
        self.update_button = QPushButton('Update plugin')
        self.update_button.setStyleSheet(
            'QPushButton { background: #2d6fba; color: white; font-size: 14px; '
            'font-weight: normal; min-height: 22px; padding: 8px 20px; '
            'border: 2px solid transparent; border-radius: 5px; }'
            'QPushButton:hover { background: #367dca; }'
            'QPushButton:focus { border-color: #b8dcff; }')
        self.update_button.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl('https://plugins.qgis.org/plugins/FlyPath/')))
        notice_layout.addWidget(self.update_button, 0, _AlignCenter)
        notice_row = QHBoxLayout()
        notice_row.addStretch()
        notice_row.addWidget(self.sync_notice, 1)
        notice_row.addStretch()
        layout.addLayout(notice_row)
        layout.addStretch(0)
        self.controls = QWidget()
        layout.addWidget(self.controls, 1)
        layout = QVBoxLayout(self.controls)
        layout.setContentsMargins(0, 0, 0, 0)
        self.connect_button = QPushButton('Connect account…')
        self.connect_button.clicked.connect(self.connect_account)
        header.addWidget(self.connect_button)
        self.disconnect_button = QPushButton('Disconnect')
        self.disconnect_button.clicked.connect(self.disconnect_account)
        header.addWidget(self.disconnect_button)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        self.why_button = QPushButton('Why FlyPath.io?')
        self.why_button.setObjectName('rcBrowseBtn')       # the panel's small secondary style
        self.why_button.setToolTip('Why FlyPath.io works alongside this plugin.')
        self.why_button.clicked.connect(self.explain_website)
        header.addWidget(self.why_button)
        layout.addLayout(header)
        layout.addWidget(self._build_signup_card())
        self.missions = QTreeWidget()
        self.missions.setStyleSheet(_LIST_STYLE)
        self.missions.setHeaderLabels(['Mission', 'Updated'])
        self.missions.setRootIsDecorated(False)
        self.missions.setColumnWidth(0, 210)
        self.missions.setSortingEnabled(True)
        self.missions.sortByColumn(1, _Descending)
        self.missions.itemSelectionChanged.connect(self.update_buttons)
        self.missions.itemDoubleClicked.connect(lambda item, column: self.import_selected())
        layout.addWidget(self.missions, 1)
        actions = QHBoxLayout()
        self.import_button = QPushButton('Open mission')
        self.import_button.clicked.connect(self.import_selected)
        actions.addWidget(self.import_button)
        self.send_button = QPushButton('Save to FlyPath…')
        self.send_button.clicked.connect(self.send_current)
        actions.addWidget(self.send_button)
        self.copy_button = QPushButton('Save as new…')
        self.copy_button.clicked.connect(lambda: self.send_current(save_as_new=True))
        actions.addWidget(self.copy_button)
        layout.addLayout(actions)
        self.link_label = QLabel()
        self.link_label.setTextFormat(_PlainText)
        self.link_label.setWordWrap(True)
        layout.addWidget(self.link_label)
        self.update_buttons()

    def _build_signup_card(self):
        """Why and where to sign up for flypath.io, for a user not connected."""
        card = QFrame()
        card.setObjectName('signupCard')
        card.setStyleSheet(_SIGNUP_STYLE)
        column = QVBoxLayout(card)
        column.setContentsMargins(12, 10, 12, 12)
        column.setSpacing(6)
        title = QLabel('New to FlyPath.io? Why use it with the plugin')
        title.setObjectName('signupTitle')
        title.setWordWrap(True)
        column.addWidget(title)
        for role, text in _ROLES:
            name = QLabel(role)
            name.setObjectName('signupRole')
            column.addWidget(name)
            line = QLabel(text)
            line.setObjectName('signupReason')
            line.setWordWrap(True)
            column.addWidget(line)
        together = QLabel(_ROLES_TOGETHER)
        together.setObjectName('signupText')
        together.setWordWrap(True)
        column.addSpacing(2)
        column.addWidget(together)
        coming = QLabel(_COMING)
        coming.setObjectName('signupComing')
        coming.setWordWrap(True)
        column.addWidget(coming)
        free = QLabel('A FlyPath.io account is free.')
        free.setObjectName('signupText')
        column.addWidget(free)
        button = QPushButton('Create a free account')
        button.setObjectName('signupButton')
        button.setToolTip(SIGNUP_URL)
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SIGNUP_URL)))
        column.addSpacing(4)
        column.addWidget(button, 0, _AlignLeft)
        hint = QLabel('Already have an account? Use Connect account above.')
        hint.setObjectName('signupHint')
        hint.setWordWrap(True)
        column.addWidget(hint)
        self.signup_card = card
        return card

    def explain_website(self):
        """Why there is a website as well as this plugin."""
        parts = ['<b>%s</b>: %s' % (role, text) for role, text in _ROLES]
        QMessageBox.information(
            self, 'Why FlyPath.io?',
            '<p>' + '</p><p>'.join(parts) + '</p><p>' + _ROLES_TOGETHER + '</p>'
            '<p>' + _COMING + '</p>'
            '<p>Create a free account at <a href="%s">flypath.io/register</a>.</p>' % SIGNUP_URL)

    def update_buttons(self):
        storage_error = None
        try:
            connected = bool(flypath_sync.load_token())
        except flypath_sync.FlypathSyncError as exc:
            connected = False
            storage_error = str(exc)
        storage_locked = bool(storage_error and 'storage is locked' in storage_error)
        incompatible = getattr(self.planner, '_sync_incompatible', False)
        link = self.planner._current_website_link()
        self.link_label.setText('Editing: %s' % link['name'] if link else 'Current mission is not linked to FlyPath.')
        self.send_button.setText('Save changes' if link else 'Save to FlyPath…')
        self.copy_button.setVisible(bool(link))
        self.connect_button.setText('Unlock account…' if storage_locked else 'Connect account…')
        self.connect_button.setVisible(not connected)
        self.disconnect_button.setText('Forget saved access…' if storage_error else 'Disconnect')
        self.disconnect_button.setVisible(connected or storage_error is not None)
        self.disconnect_button.setToolTip(
            'Forget access on this device and unlink the mission. This does not '
            'revoke the token on the website. Rotate old tokens to invalidate backups.')
        self.refresh_button.setEnabled(connected and not incompatible)
        self.import_button.setEnabled(connected and not incompatible and self.missions.currentItem() is not None)
        # Saving stays clickable without a preview: the planner answers a
        # click with the reason, which a disabled button cannot do (Qt
        # shows no tooltip on a disabled widget).
        self.send_button.setToolTip(
            'Update this mission on FlyPath with the previewed plan. Newer website '
            'edits are checked first.' if link else
            'Save the previewed plan to FlyPath as a new mission.')
        self.copy_button.setToolTip(
            'Save the previewed plan as a separate FlyPath mission, leaving '
            '"%s" untouched.' % link['name'] if link else '')
        self.import_button.setToolTip('Open the selected mission in the planner.' if
                                      self.import_button.isEnabled() else
                                      'Select a mission in the list first.')
        self.send_button.setEnabled(not incompatible)
        self.copy_button.setEnabled(not incompatible)
        if incompatible:
            self.status.setText(flypath_sync.SYNC_MISMATCH_MESSAGE)
        elif not connected:
            self.status.setText(storage_error or 'Connect your FlyPath account to browse missions.')
        self.signup_card.setVisible(not connected and storage_error is None)
        self.why_button.setVisible(connected)
        self.sync_notice.setVisible(bool(incompatible))
        self.status.setVisible(not incompatible)
        self.controls.setVisible(not incompatible)
        self.layout().setStretch(0, 1 if incompatible else 0)
        self.layout().setStretch(3, 1 if incompatible else 0)

    def show_sync_incompatible(self):
        self.update_buttons()
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QTabWidget):
            parent = parent.parentWidget()
        if parent is not None:
            parent.setCurrentWidget(self)

    def connect_account(self):
        if self.planner._web_token():
            self.refresh()

    def disconnect_account(self):
        error = None
        try:
            flypath_sync.save_token('')
        except flypath_sync.FlypathSyncError as exc:
            error = str(exc)
        finally:
            self.planner._website_link = None
            self.planner._sync_incompatible = False
            self.missions.clear()
            self.update_buttons()
        if error:
            self.disconnect_button.setVisible(True)
            self.status.setText(error)
            QMessageBox.warning(self, 'Disconnect incomplete', error)
        else:
            self.status.setText('Access forgotten on this device. The token is still valid on the website: choose Plugin token in the account menu to revoke or rotate it and invalidate other copies and old settings backups.')

    def refresh(self):
        item = self.missions.currentItem()
        link = self.planner._current_website_link()
        keep = item.data(0, _UserRole) if item is not None else (link or {}).get('id')
        self.missions.clear()
        try:
            connected = bool(flypath_sync.load_token())
        except flypath_sync.FlypathSyncError as exc:
            if 'storage is locked' in str(exc) and self.planner._web_token():
                return self.refresh()
            connected = False
        if not connected:
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
                if mission.get('id') == keep:
                    self.missions.setCurrentItem(item)
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
