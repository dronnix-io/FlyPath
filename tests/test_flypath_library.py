"""Run with QGIS Python: python tests/test_flypath_library.py (no network)."""
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
package = Path(__file__).resolve().parents[1].name


def test_library():
    try:
        library_module = importlib.import_module(package + '.flypath_library')
        from qgis.PyQt.QtWidgets import QApplication, QWidget, QTabWidget
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QApplication.instance() or QApplication([])
    dock = QTabWidget()
    planner = QWidget(dock)
    dock.addTab(planner, 'Planner')
    planner._website_link = None
    planner._current_website_link = lambda: planner._website_link
    planner._preview_layer_ids = []
    planner._missions = []
    planner._web_date = lambda value: value or ''
    planner._run_web = lambda title, work: work('fake')
    imported = []
    planner._on_load_from_website = lambda mission_id: imported.append(mission_id) or True
    with patch.object(library_module.flypath_sync, 'load_token', return_value='fake') as token, \
         patch.object(library_module.flypath_sync, 'load_base_url', return_value='https://example.test'), \
         patch.object(library_module.flypath_sync, 'save_token') as save, \
         patch.object(library_module.flypath_sync, 'list_library', return_value={'missions': [
             {'id': 7, 'name': 'Same name', 'updated_at': '2026-09-09'},
             {'id': 8, 'name': 'Same name', 'updated_at': '2026-09-09'},
             {'id': 10, 'name': 'Future survey', 'mapping_style': 'future-survey'},
             {'id': 9, 'name': 'Survey part - 1', 'display_name': 'Part 01',
              'updated_at': '2026-09-09', 'project_id': 2},
         ], 'projects': [{'id': 2, 'name': 'Survey'}, {'id': 3, 'name': 'Survey'}]}):
        library = library_module.MissionLibrary(planner, parent=dock)
        dock.addTab(library, 'My missions')
        library.missionOpened.connect(lambda: dock.setCurrentWidget(planner))
        assert dock.currentWidget() is planner
        assert not library.isWindow(), 'Library must be embedded in the dock'
        dock.setCurrentWidget(library)
        library.refresh()
        unlock = Mock(return_value='fake')
        planner._web_token = unlock
        token.side_effect = [
            library_module.flypath_sync.FlypathSyncError('storage is locked'),
            'fake', 'fake',
        ]
        library.refresh()
        unlock.assert_called_once_with()
        token.side_effect = None
        token.return_value = 'fake'
        # Save stays reachable so the planner can explain why Preview is needed.
        assert library.send_button.isEnabled()
        assert not library.import_button.isEnabled()
        groups = {library.missions.topLevelItem(i).data(0, library_module._ProjectRole):
                  library.missions.topLevelItem(i) for i in range(library.missions.topLevelItemCount())}
        assert set(groups) == {None, 2, 3}
        assert library.missions.topLevelItemCount() == 5
        assert library.selection_hint.text() == 'Select a mission to open it'
        assert groups[2].text(0) == 'Survey (1 mission)'
        assert groups[3].text(0) == 'Survey (0 missions)'
        assert not groups[3].icon(0).isNull()
        assert not any(group.isExpanded() for group in groups.values()), 'Groups start collapsed'
        assert groups[3].childCount() == 0, 'Empty projects stay visible'
        assert groups[2].toolTip(0) == 'Survey'
        assert groups[2].child(0).data(0, library_module._UserRole) == 9
        assert groups[2].child(0).text(0) == 'Part 01'
        assert not groups[2].child(0).icon(0).isNull()
        assert groups[2].child(0).toolTip(0) == 'Survey part - 1'
        library.missions.setCurrentItem(groups[2])
        assert not library.import_button.isEnabled(), 'A project is not a mission'
        library.import_selected()
        assert not imported
        standalone = {library.missions.topLevelItem(i).data(0, library_module._UserRole):
                      library.missions.topLevelItem(i) for i in range(library.missions.topLevelItemCount())}
        unsupported = standalone[10]
        assert all(not standalone[mid].icon(0).isNull() for mid in (7, 8, 10))
        assert 'Unsupported in this plugin' in unsupported.text(0)
        library.missions.setCurrentItem(unsupported)
        assert library.import_button.text() == 'Open on website'
        with patch.object(library_module.QDesktopServices, 'openUrl') as open_url:
            library.import_button.click()
            assert open_url.call_args.args[0].toString() == 'https://example.test/planner/?mission=10'
        assert not imported
        groups[2].setExpanded(True)
        library.missions.sortByColumn(0, library_module._Descending)
        mission = standalone[8]
        library.missions.setCurrentItem(mission)
        assert library.import_button.isEnabled()
        library.import_selected()
        assert imported == [8], 'Duplicate labels must retain their own mission ID'
        assert dock.currentWidget() is planner, 'Opening a mission returns to Planner'
        library.refresh()
        assert library.missions.currentItem().data(0, library_module._UserRole) == 8
        assert library.missions.currentItem().parent() is None
        assert library.missions.sortColumn() == 0
        assert library.missions.header().sortIndicatorOrder() == library_module._Descending
        groups = {library.missions.topLevelItem(i).data(0, library_module._ProjectRole):
                  library.missions.topLevelItem(i) for i in range(library.missions.topLevelItemCount())}
        assert groups[2].isExpanded(), 'Refresh keeps opened projects'
        assert not groups[3].isExpanded(), 'Refresh keeps closed projects'
        library.missions.setCurrentItem(groups[2].child(0))
        library.refresh()
        assert library.missions.currentItem().data(0, library_module._UserRole) == 9
        assert library.missions.currentItem().parent().isExpanded()
        with patch.object(planner, '_run_web', return_value=None):
            library.refresh()
        assert library.missions.currentItem().data(0, library_module._UserRole) == 9
        groups = {library.missions.topLevelItem(i).data(0, library_module._ProjectRole):
                  library.missions.topLevelItem(i) for i in range(library.missions.topLevelItemCount())}
        library.missions.setCurrentItem(groups[3])
        library.refresh()
        assert library.missions.currentItem().data(0, library_module._ProjectRole) == 3
        library.missions.clearSelection()
        library.refresh()
        assert library.selection_hint.text() == 'Select a mission to open it'
        assert not library.import_button.isEnabled()
        planner._preview_layer_ids = ['preview']
        planner._missions = [[1]]
        library.update_buttons()
        assert library.send_button.isEnabled()
        planner._website_link = {'id': 8, 'revision': 1, 'name': 'Same name'}
        library.update_buttons()
        assert library.send_button.text() == 'Save changes'
        assert not library.copy_button.isHidden()
        token.return_value = ''
        library.disconnect_account()
        save.assert_called_once_with('')
        assert library.missions.topLevelItemCount() == 0
        assert not library.import_button.isEnabled()
        assert not library.refresh_button.isEnabled()
        library.close()
    dock.close()
    app.processEvents()


def test_new_mission_destination():
    try:
        library = importlib.import_module(package + '.flypath_library')
        from qgis.PyQt.QtCore import QTimer
        from qgis.PyQt.QtWidgets import QApplication, QDialog, QLineEdit, QComboBox, QDialogButtonBox
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QApplication.instance() or QApplication([])
    projects = [{'id': 2, 'name': 'Survey'}, {'id': 3, 'name': 'Survey'}]
    observed = []

    def choose_project():
        dialog = app.activeModalWidget()
        name = dialog.findChild(QLineEdit)
        project = dialog.findChild(QComboBox)
        save = dialog.findChild(QDialogButtonBox).button(library._Save)
        observed.append(project.currentData())
        name.setText('  ')
        observed.append(save.isEnabled())
        name.setText('  New mission  ')
        project.setCurrentIndex(2)
        save.click()

    QTimer.singleShot(0, choose_project)
    assert library.new_mission_details(None, 'Survey copy', projects, project_id=2) == ('New mission', 3)
    assert observed == [2, False], 'Save requires a name; duplicate project names retain their IDs'
    QTimer.singleShot(0, lambda: app.activeModalWidget().reject())
    assert library.new_mission_details(None, 'Survey', projects) is None
    QTimer.singleShot(0, lambda: app.activeModalWidget().accept())
    assert library.new_mission_details(None, 'Legacy', []) == ('Legacy', None)
    for widget in app.topLevelWidgets():
        if isinstance(widget, QDialog):
            widget.close()


def test_incompatible_sync_notice():
    try:
        library_module = importlib.import_module(package + '.flypath_library')
        controller = importlib.import_module(package + '.website_sync_controller')
        from qgis.PyQt.QtWidgets import QApplication, QWidget, QTabWidget
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QApplication.instance() or QApplication([])
    dock = QTabWidget()
    planner = QWidget(dock)
    dock.addTab(planner, 'Planner')
    planner._current_website_link = lambda: None
    planner._web_token = lambda: 'fake'
    planner._update_web_buttons = lambda: library.update_buttons()
    with patch.object(library_module.flypath_sync, 'load_token', return_value='fake') as token, \
         patch.object(library_module.flypath_sync, 'load_base_url', return_value='https://example.test'), \
         patch.object(library_module.flypath_sync, 'save_token'), \
         patch.object(controller.QMessageBox, 'warning') as warning:
        library = library_module.MissionLibrary(planner, parent=dock)
        planner._mission_library = library
        planner._run_web = lambda title, work: controller.WebsiteSyncLifecycleMixin._run_web(
            planner, title, work)
        dock.addTab(library, 'My missions')
        dock.currentChanged.connect(
            lambda index: library.refresh() if dock.widget(index) is library else None)
        error = library_module.flypath_sync.FlypathSyncError('server message', status=426)

        def incompatible(_token):
            raise error

        assert controller.WebsiteSyncLifecycleMixin._run_web(planner, 'Save', incompatible) is None
        assert dock.currentWidget() is library
        assert library.status.text() == library_module.flypath_sync.SYNC_MISMATCH_MESSAGE
        assert library.sync_notice.isVisibleTo(dock)
        assert library.controls.isHidden()
        assert library.status.isHidden()
        with patch.object(library_module.QDesktopServices, 'openUrl') as open_url:
            library.update_button.click()
            assert open_url.call_args.args[0].toString() == 'https://plugins.qgis.org/plugins/FlyPath/'
        assert not library.refresh_button.isEnabled()
        assert not library.send_button.isEnabled()
        assert not library.copy_button.isEnabled()
        work = Mock()
        assert controller.WebsiteSyncLifecycleMixin._run_web(planner, 'Save', work) is None
        work.assert_not_called()
        warning.assert_not_called()
        token.return_value = ''
        library.disconnect_account()
        assert not library.sync_notice.isVisibleTo(dock)
        assert not library.controls.isHidden()
        assert not library.status.isHidden()
        assert not planner._sync_incompatible
        library.close()
    dock.close()
    app.processEvents()


def test_incompatible_sync_notice_wraps_without_clipping():
    try:
        library_module = importlib.import_module(package + '.flypath_library')
        dialog_module = importlib.import_module(package + '.flypath_dialog')
        from qgis.PyQt.QtGui import QFont, QFontDatabase
        from qgis.PyQt.QtWidgets import QApplication, QWidget, QTabWidget, QLabel
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QApplication.instance() or QApplication([])
    previous_font = QFont(app.font())
    if os.name == 'nt':
        font_path = Path(os.environ['WINDIR']) / 'Fonts' / 'segoeui.ttf'
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont('Segoe UI', 9))
    for width in (250, 400, 700):
        dock = QTabWidget()
        dock.setStyleSheet(dialog_module.STYLESHEET)
        planner = QWidget(dock)
        planner._sync_incompatible = True
        planner._current_website_link = lambda: None
        dock.addTab(planner, 'Planner')
        with patch.object(library_module.flypath_sync, 'load_token', return_value='fake'):
            library = library_module.MissionLibrary(planner, dock)
        dock.addTab(library, 'My missions')
        dock.resize(width, 600)
        dock.show()
        dock.setCurrentWidget(library)
        app.processEvents()
        body = next(label for label in library.sync_notice.findChildren(QLabel)
                    if label.text() == 'A newer version may be available.')
        assert body.height() >= body.heightForWidth(body.width()), width
        dock.close()
    app.setFont(previous_font)


if __name__ == '__main__':
    test_library()
    test_new_mission_destination()
    test_incompatible_sync_notice()
    test_incompatible_sync_notice_wraps_without_clipping()
    print('Mission library smoke check passed')
