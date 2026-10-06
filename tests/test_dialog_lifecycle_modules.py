"""Focused checks for dialog behavior extracted into lifecycle modules."""

import ast
import importlib
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


ROOT = Path(__file__).resolve().parents[1]


def _class(path, name):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == name)


def test_dialog_lifecycle_ownership_without_qgis():
    dialog = _class(ROOT / 'flypath_dialog.py', 'FlyPathDialog')
    survey = _class(ROOT / 'survey_controller.py', 'SurveyLifecycleMixin')
    website = _class(ROOT / 'website_sync_controller.py', 'WebsiteSyncLifecycleMixin')
    orbit = _class(ROOT / 'orbit_controller.py', 'OrbitMixin')

    assert [base.id for base in dialog.bases] == [
        'SurveyLifecycleMixin', 'WebsiteSyncLifecycleMixin', 'OrbitMixin', 'QWidget']

    dialog_methods = {node.name for node in dialog.body if isinstance(node, ast.FunctionDef)}
    survey_methods = {node.name for node in survey.body if isinstance(node, ast.FunctionDef)}
    website_methods = {node.name for node in website.body if isinstance(node, ast.FunctionDef)}
    orbit_methods = {node.name for node in orbit.body if isinstance(node, ast.FunctionDef)}

    assert {'_on_draw_polygon', '_on_draw_line', '_set_survey_geometry'} <= survey_methods
    assert {'_on_send_to_website', '_on_load_from_website', '_website_payload'} <= website_methods
    assert {'_apply_orbit_layout', '_orbit_request_from_ui', 'set_orbit_centre'} <= orbit_methods
    assert not dialog_methods & (survey_methods | website_methods | orbit_methods)


def test_dialog_lifecycle_modules():
    try:
        from qgis.core import QgsGeometry
        package = Path(__file__).resolve().parents[1].name
        dialog = importlib.import_module(package + '.flypath_dialog')
        survey = importlib.import_module(package + '.survey_controller')
        website = importlib.import_module(package + '.website_sync_controller')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc

    assert issubclass(dialog.FlyPathDialog, survey.SurveyLifecycleMixin)
    assert issubclass(dialog.FlyPathDialog, website.WebsiteSyncLifecycleMixin)

    class SurveyHarness(survey.SurveyLifecycleMixin):
        pass

    harness = SurveyHarness()
    harness._survey_line = QgsGeometry.fromWkt(
        'MULTILINESTRING((0 0,1 1,2 2,3 3),(4 4,5 5,6 6))')
    harness._corridor_breaks = {(0, 1), (0, 0), (1, 1), (9, 9)}
    assert [part.asWkt() for part in harness._corridor_sublines()] == [
        'LineString (0 0, 1 1)',
        'LineString (1 1, 2 2, 3 3)',
        'LineString (4 4, 5 5)',
        'LineString (5 5, 6 6)',
    ]
    assert website.WebsiteSyncLifecycleMixin._web_date(None) == 'never saved'
    assert website.WebsiteSyncLifecycleMixin._web_date('unparseable') == 'unparseable'


def test_shared_planning_dispatch_and_failure_without_qgis():
    """Run the dialog's planning entry points without importing Qt."""
    dialog = _class(ROOT / 'flypath_dialog.py', 'FlyPathDialog')
    methods = [node for node in dialog.body if isinstance(node, ast.FunctionDef)
               and node.name in ('_planning_request_from_ui', '_plan_shared',
                                 '_uses_shared_planning', '_export_settings')]
    adapter = SimpleNamespace(plan=Mock(), PlanningError=ValueError)
    warnings = SimpleNamespace(warning=Mock())
    namespace = {'planning_adapter': adapter, 'QMessageBox': warnings,
                 'registry': SimpleNamespace(get=lambda name: name),
                 'mission_export': SimpleNamespace(ExportSettings=SimpleNamespace)}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(ROOT / 'flypath_dialog.py'),
                 'exec'), namespace)
    request = {'operation': 'plan_orbit'}
    result = {'validation': {'export_allowed': True}}
    planner = SimpleNamespace(
        _mission_kind=lambda: 'orbit', _orbit_request_from_ui=lambda: request,
        _apply_planning_result=Mock(), _set_info=Mock(),
        _planning=SimpleNamespace(plan_failed=Mock()),
        _waypoints=[(0, 0)], _missions=[[(0, 0)]], _shot_spacing_m=5)
    widget = SimpleNamespace(value=lambda: 25, currentText=lambda: 'DJI Mini 4 Pro',
                             isChecked=lambda: False)
    for name in ('terrainFollowCheck', 'droneModelCombo', 'altitudeSpin', 'speedSpin',
                 'finishActionCombo', 'rcLostActionCombo', 'orbitTiltSpin',
                 'sideOverlapSpin', 'directionSpin', 'marginSpin'):
        setattr(planner, name, widget)
    planner._mission_type = lambda: 'full'
    planner._path_curved = lambda: True
    planner._front_overlap_fraction = lambda: .7
    assert namespace['_uses_shared_planning'](planner)
    planner._planning.result = {'route': {'waypoints': [{'heading_deg': 180}]}}
    assert namespace['_export_settings'](planner).headings == (180,)
    planner._planning.result['route']['waypoints'][0]['heading_deg'] = -90
    assert namespace['_export_settings'](planner).headings == (-90,)
    planner._planning.result = None
    assert namespace['_export_settings'](planner).headings is None
    planner._planning_request_from_ui = lambda: namespace['_planning_request_from_ui'](planner)
    adapter.plan.return_value = result
    assert namespace['_plan_shared'](planner) is result
    adapter.plan.assert_called_once_with(request)
    planner._apply_planning_result.assert_called_once_with(request, result)
    result['validation'] = {'export_allowed': False, 'errors': [{'message': 'Too many waypoints'}]}
    assert namespace['_plan_shared'](planner) is result
    planner._set_info.assert_called_once_with('Too many waypoints')
    adapter.plan.side_effect = ValueError('Invalid radius')
    assert namespace['_plan_shared'](planner, silent=False) is None
    planner._planning.plan_failed.assert_called_once_with()
    assert planner._waypoints == [] and planner._missions == []
    assert planner._shot_spacing_m == 0
    warnings.warning.assert_called_once_with(planner, 'Cannot Plan Orbit', 'Invalid radius')


if __name__ == '__main__':
    test_dialog_lifecycle_modules()
    print('Dialog lifecycle module checks passed')
