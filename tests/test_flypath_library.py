"""Run with QGIS Python: python tests/test_flypath_library.py (no network)."""
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

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
         patch.object(library_module.flypath_sync, 'list_missions', return_value=[
             {'id': 7, 'name': 'Same name', 'updated_at': '2026-09-09'},
             {'id': 8, 'name': 'Same name', 'updated_at': '2026-09-09'},
         ]):
        library = library_module.MissionLibrary(planner, parent=dock)
        dock.addTab(library, 'My missions')
        library.missionOpened.connect(lambda: dock.setCurrentWidget(planner))
        assert dock.currentWidget() is planner
        assert not library.isWindow(), 'Library must be embedded in the dock'
        dock.setCurrentWidget(library)
        library.refresh()
        # Save stays reachable so the planner can explain why Preview is needed.
        assert library.send_button.isEnabled()
        assert not library.import_button.isEnabled()
        library.missions.setCurrentItem(library.missions.topLevelItem(1))
        assert library.import_button.isEnabled()
        library.import_selected()
        assert imported == [8], 'Duplicate labels must retain their own mission ID'
        assert dock.currentWidget() is planner, 'Opening a mission returns to Planner'
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


if __name__ == '__main__':
    test_library()
    print('Mission library smoke check passed')
