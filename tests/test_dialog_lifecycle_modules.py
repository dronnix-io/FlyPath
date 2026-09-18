"""Focused checks for dialog behavior extracted into lifecycle modules."""

import ast
import importlib
import os
from pathlib import Path
import sys
import unittest

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

    assert [base.id for base in dialog.bases] == [
        'SurveyLifecycleMixin', 'WebsiteSyncLifecycleMixin', 'QWidget']

    dialog_methods = {node.name for node in dialog.body if isinstance(node, ast.FunctionDef)}
    survey_methods = {node.name for node in survey.body if isinstance(node, ast.FunctionDef)}
    website_methods = {node.name for node in website.body if isinstance(node, ast.FunctionDef)}

    assert {'_on_draw_polygon', '_on_draw_line', '_set_survey_geometry'} <= survey_methods
    assert {'_on_send_to_website', '_on_load_from_website', '_website_payload'} <= website_methods
    assert not dialog_methods & (survey_methods | website_methods)


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


if __name__ == '__main__':
    test_dialog_lifecycle_modules()
    print('Dialog lifecycle module checks passed')
