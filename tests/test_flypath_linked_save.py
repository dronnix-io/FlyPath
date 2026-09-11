"""QGIS-runtime regression for linked saves and conflict choices; no network."""
import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace, MethodType
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_linked_save():
    package = Path(__file__).resolve().parents[1].name
    try:
        module = importlib.import_module(package + '.flypath_dialog')
        library = importlib.import_module(package + '.flypath_library')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    sync = module.flypath_sync
    planner = SimpleNamespace(
        _website_link=None, _preview_layer_ids=['preview'], _missions=[[1]],
        _update_web_buttons=lambda: None,
        _website_payload=lambda name: {'name': name, 'settings': {'altitude': 80}},
        _run_web=lambda title, work, **kwargs: work('token-a'),
        _apply_website_mission=lambda mission: [],
    )
    for name in ('_current_website_link', '_remember_website_mission',
                 '_on_send_to_website', '_resolve_website_conflict', '_on_load_from_website', '_forget_website_mission'):
        setattr(planner, name, MethodType(getattr(module.FlyPathDialog, name), planner))
    with patch.object(sync, 'load_token', return_value='token-a') as token, \
         patch.object(sync, 'load_base_url', return_value='https://example.test'), \
         patch.object(sync, 'push_mission', return_value={'id': 7, 'revision': 1, 'name': 'Survey'}) as create, \
         patch.object(sync, 'update_mission', return_value={'id': 7, 'revision': 2, 'name': 'Survey'}) as update, \
         patch.object(module.QInputDialog, 'getText', return_value=('Survey', True)), \
         patch.object(module.QMessageBox, 'question', return_value=module._MB_NO) as question, \
         patch.object(module.QMessageBox, 'information'), \
         patch.object(module.QMessageBox, 'warning'), \
         patch.object(library, 'conflict_choice', return_value=None) as choice:
        assert planner._on_send_to_website()
        assert planner._website_link['id'] == 7
        assert planner._on_send_to_website()
        assert create.call_count == 1, 'Repeat save must update, not create'
        assert update.call_args.args[2:4] == (7, 1)
        assert planner._website_link['revision'] == 2

        previous = planner._website_link.copy()
        update.side_effect = sync.FlypathSyncError('Changed elsewhere', status=409)
        assert not planner._on_send_to_website()
        assert planner._website_link == previous
        assert planner._missions == [[1]]
        assert create.call_count == 1

        choice.return_value = 'reload'
        with patch.object(planner, '_on_load_from_website', return_value=True) as load:
            assert not planner._on_send_to_website()
            load.assert_not_called()  # default answer preserves local edits
            question.return_value = module._MB_YES
            assert planner._on_send_to_website()
            load.assert_called_once_with(7)
        question.return_value = module._MB_NO

        choice.return_value = 'copy'
        create.return_value = {'id': 8, 'revision': 1, 'name': 'Survey copy'}
        assert planner._on_send_to_website()
        assert create.call_count == 2
        assert planner._website_link['id'] == 8

        # Opening a mission establishes its ID/revision for the next edit.
        with patch.object(sync, 'get_mission', return_value={'id': 9, 'revision': 6, 'name': 'Opened'}):
            assert planner._on_load_from_website(9)
            assert planner._website_link['id'] == 9
            assert planner._website_link['revision'] == 6

        planner._forget_website_mission()
        assert planner._website_link is None
        planner._remember_website_mission({'id': 9, 'revision': 6, 'name': 'Opened'})
        token.return_value = 'token-b'
        assert planner._current_website_link() is None, 'Never update a link under another account'


if __name__ == '__main__':
    test_linked_save()
    print('Linked mission save check passed')
