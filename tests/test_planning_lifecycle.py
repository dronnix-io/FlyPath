"""Checks for the planning lifecycle through its public interface."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from FlyPath.planning_lifecycle import PlanningLifecycle


def test_import_edit_regenerate_sequence():
    lifecycle = PlanningLifecycle()
    saved_request = {'locations': {'launch': [1, 2]}}
    saved_result = {'validation': {'export_allowed': True}}

    lifecycle.begin_import()
    lifecycle.record(saved_request, saved_result)
    lifecycle.preserve_imported_route()
    assert lifecycle.begin_preview(has_saved_route=True) == 'restore'
    assert lifecycle.result is saved_result

    lifecycle.settings_changed()
    assert lifecycle.save_requires_regeneration()
    assert lifecycle.export_issue() == 'regeneration_required'
    assert lifecycle.begin_preview(has_saved_route=True) == 'regenerate'

    regenerated = {'validation': {'export_allowed': True}}
    lifecycle.record(saved_request, regenerated)
    assert not lifecycle.save_requires_regeneration()
    assert lifecycle.export_issue() is None
    assert lifecycle.result is regenerated


if __name__ == '__main__':
    test_import_edit_regenerate_sequence()
    print('Planning lifecycle check passed')
