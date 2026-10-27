"""Website mission library embedded in the FlyPath dock."""

from qgis.PyQt.QtCore import Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QFrame, QGridLayout, QWidget, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QMessageBox, QDialog, QDialogButtonBox,
    QLineEdit, QComboBox, QFormLayout, QStyle,
)

from . import flypath_sync

try:
    _UserRole = Qt.ItemDataRole.UserRole
    _PlainText = Qt.TextFormat.PlainText
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
    _AlignLeft = Qt.AlignmentFlag.AlignLeft
    _AlignTopLeft = Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
    _ActionRole = QMessageBox.ButtonRole.ActionRole
    _Cancel = QMessageBox.StandardButton.Cancel
    _Information = QMessageBox.Icon.Information
    _Descending = Qt.SortOrder.DescendingOrder
    _Save = QDialogButtonBox.StandardButton.Save
    _DialogCancel = QDialogButtonBox.StandardButton.Cancel
    _FolderIcon = QStyle.StandardPixmap.SP_DirIcon
except AttributeError:
    _UserRole = getattr(Qt, 'UserRole')
    _PlainText = getattr(Qt, 'PlainText')
    _AlignCenter = getattr(Qt, 'AlignCenter')
    _AlignLeft = getattr(Qt, 'AlignLeft')
    _AlignTopLeft = getattr(Qt, 'AlignTop') | getattr(Qt, 'AlignLeft')
    _ActionRole = getattr(QMessageBox, 'ActionRole')
    _Cancel = getattr(QMessageBox, 'Cancel')
    _Information = getattr(QMessageBox, 'Information')
    _Descending = getattr(Qt, 'DescendingOrder')
    _Save = getattr(QDialogButtonBox, 'Save')
    _DialogCancel = getattr(QDialogButtonBox, 'Cancel')
    _FolderIcon = getattr(QStyle, 'SP_DirIcon')

_ProjectRole = _UserRole + 1
_UnsupportedRole = _UserRole + 2


SIGNUP_URL = flypath_sync.DEFAULT_BASE_URL + '/register/'
# Why there are two tools, as short labelled lines. The last one is coming.
_WHY = (
    ('QGIS plugin', 'Plan with your GIS data; send to the DJI RC.'),
    ('FlyPath.io', 'Archive, share and plan in any browser.'),
    ('Both', 'Same engine: missions open unchanged in each.'),
    ('Coming', 'Cloud 2D maps and 3D models of flown missions.'),
)
_SIGNUP_STYLE = """
QFrame#signupCard {
    background-color: #1C2836; border: 1px solid #2F4A6E;
    border-left: 3px solid #3F9BFF; border-radius: 5px;
}
QLabel#signupTitle { color: #FFFFFF; font-size: 13px; font-weight: bold; }
QLabel#signupText { color: #C8D6E8; }
QLabel#signupRole { color: #9CC4F2; font-weight: bold; }
QLabel#signupComing { color: #F5C451; font-weight: bold; }
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


def new_mission_details(parent, name, projects, project_id=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle('Save as new on FlyPath')
    layout = QFormLayout(dialog)
    name_input = QLineEdit(name)
    layout.addRow('Mission name:', name_input)
    destination = QComboBox()
    destination.addItem('Standalone mission', None)
    for project in projects:
        destination.addItem(project['name'], project['id'])
    destination.setCurrentIndex(max(0, destination.findData(project_id)))
    layout.addRow('Project:', destination)
    buttons = QDialogButtonBox(_Save | _DialogCancel)
    buttons.button(_Save).setEnabled(bool(name.strip()))
    name_input.textChanged.connect(lambda text: buttons.button(_Save).setEnabled(bool(text.strip())))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addRow(buttons)
    name_input.selectAll()
    if dialog.exec():
        return name_input.text().strip(), destination.currentData()
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
        self.missions.setRootIsDecorated(True)
        self.missions.setColumnWidth(0, 210)
        self.missions.setSortingEnabled(True)
        self.missions.sortByColumn(1, _Descending)
        self.missions.itemSelectionChanged.connect(self.update_buttons)
        self.missions.itemDoubleClicked.connect(lambda item, column: self.import_selected())
        layout.addWidget(self.missions, 1)
        self.selection_hint = QLabel('Select a mission to open it')
        self.selection_hint.setWordWrap(True)
        layout.addWidget(self.selection_hint)
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
        title = QLabel('Why use FlyPath.io too?')
        title.setObjectName('signupTitle')
        column.addWidget(title)
        rows = QGridLayout()
        rows.setHorizontalSpacing(10)
        rows.setVerticalSpacing(4)
        for row, (name, line) in enumerate(_WHY):
            label = QLabel(name)
            label.setObjectName('signupComing' if name == 'Coming' else 'signupRole')
            rows.addWidget(label, row, 0, _AlignTopLeft)
            value = QLabel(line)
            value.setObjectName('signupReason')
            value.setWordWrap(True)
            rows.addWidget(value, row, 1)
        rows.setColumnStretch(1, 1)
        column.addLayout(rows)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        button = QPushButton('Create a free account')
        button.setObjectName('signupButton')
        button.setToolTip(SIGNUP_URL)
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SIGNUP_URL)))
        actions.addWidget(button)
        hint = QLabel('Have one? Use Connect account.')
        hint.setObjectName('signupHint')
        actions.addWidget(hint, 1)
        column.addSpacing(4)
        column.addLayout(actions)
        self.signup_card = card
        return card

    def explain_website(self):
        """Why there is a website as well as this plugin."""
        rows = ''.join('<tr><td style="padding-right:10px;"><b>%s</b></td><td>%s</td></tr>'
                       % (name, line) for name, line in _WHY)
        box = QMessageBox(self)
        box.setIcon(_Information)
        box.setWindowTitle('Why FlyPath.io?')
        # Qt's default dark blue link is hard to read on a dark theme: pick a
        # blue that suits the box's own background.
        box.ensurePolished()
        dark = box.palette().window().color().lightness() < 128
        box.setText('<table cellspacing="3">%s</table><p>Free account: '
                    '<a href="%s" style="color:%s;">flypath.io/register</a></p>'
                    % (rows, SIGNUP_URL, '#7FB3E8' if dark else '#1F5FA8'))
        box.exec()

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
        selected = self.missions.currentItem()
        is_mission = selected is not None and selected.isSelected() and selected.data(0, _UserRole) is not None
        unsupported = is_mission and selected.data(0, _UnsupportedRole)
        self.selection_hint.setText('Unsupported in this plugin' if unsupported else
                                    '' if is_mission else 'Select a mission to open it')
        self.import_button.setText('Open on website' if unsupported else 'Open mission')
        self.import_button.setEnabled(connected and not incompatible and is_mission)
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
        self.import_button.setToolTip('Open the selected mission on FlyPath.' if unsupported else
                                      'Open the selected mission in the planner.' if
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
        if item is not None and not item.isSelected():
            item = None
        keep = item.data(0, _UserRole) if item is not None else None
        keep_project = item.data(0, _ProjectRole) if item is not None else None
        expanded = {group.data(0, _ProjectRole)
                    for i in range(self.missions.topLevelItemCount())
                    if (group := self.missions.topLevelItem(i)).isExpanded()}
        try:
            connected = bool(flypath_sync.load_token())
        except flypath_sync.FlypathSyncError as exc:
            if 'storage is locked' in str(exc) and self.planner._web_token():
                return self.refresh()
            connected = False
        if not connected:
            self.missions.clear()
            self.update_buttons()
            return
        self.status.setText('Loading missions…')
        library = self.planner._run_web(
            'FlyPath mission library',
            lambda token: flypath_sync.list_library(flypath_sync.load_base_url(), token))
        if library is not None:
            self.missions.clear()
            self.missions.setSortingEnabled(False)
            groups = {}
            for project in library['projects']:
                group = QTreeWidgetItem([project['name'], ''])
                group.setData(0, _ProjectRole, project['id'])
                group.setToolTip(0, project['name'])
                group.setIcon(0, self.style().standardIcon(_FolderIcon))
                self.missions.addTopLevelItem(group)
                groups[project['id']] = group
                if keep is None and project['id'] == keep_project:
                    self.missions.setCurrentItem(group)
            for mission in library['missions']:
                project_id = mission.get('project_id')
                item = QTreeWidgetItem([
                    mission.get('display_name') or mission.get('name') or 'Untitled mission',
                    self.planner._web_date(mission.get('updated_at')),
                ])
                item.setData(0, _UserRole, mission.get('id'))
                item.setData(0, _ProjectRole, project_id)
                item.setToolTip(0, mission.get('name') or item.text(0))
                unsupported = ('mapping_style' in mission and
                               mission['mapping_style'] not in flypath_sync.SUPPORTED_MAPPING_STYLES)
                item.setData(0, _UnsupportedRole, unsupported)
                if unsupported:
                    item.setText(0, item.text(0) + ' — Unsupported in this plugin')
                    item.setToolTip(0, item.toolTip(0) + '\nUnsupported in this plugin. Open on website.')
                if project_id in groups:
                    groups[project_id].addChild(item)
                    groups[project_id].setText(1, max(groups[project_id].text(1), item.text(1)))
                else:
                    self.missions.addTopLevelItem(item)
                if mission.get('id') == keep:
                    self.missions.setCurrentItem(item)
            for project_id, group in groups.items():
                count = group.childCount()
                group.setText(0, '%s (%s %s)' % (group.text(0), count, 'mission' if count == 1 else 'missions'))
                group.setExpanded(project_id in expanded)
            self.missions.setSortingEnabled(True)
            self.status.setText('%s projects · %s missions · flypath.io'
                                % (len(library['projects']), len(library['missions'])))
        else:
            self.status.setText('Could not load missions. Try Refresh.')
        self.update_buttons()

    def import_selected(self):
        item = self.missions.currentItem()
        if item is not None and item.isSelected() and item.data(0, _UserRole) is not None:
            if item.data(0, _UnsupportedRole):
                QDesktopServices.openUrl(QUrl(flypath_sync.mission_url(
                    item.data(0, _UserRole), flypath_sync.load_base_url())))
                return
            if self.planner._on_load_from_website(item.data(0, _UserRole)):
                self.missionOpened.emit()

    def send_current(self, save_as_new=False):
        if self.planner._on_send_to_website(save_as_new=save_as_new):
            self.refresh()
