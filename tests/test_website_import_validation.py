"""Installed-QGIS check: rejected imports preserve the actual planner."""
import copy
import importlib
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_import_preserves_planner():
    try:
        from qgis.core import QgsApplication, QgsProject
        from qgis.gui import QgsMapCanvas
        module = importlib.import_module(Path(__file__).resolve().parents[1].name + '.flypath_dialog')
    except ImportError as exc:
        raise unittest.SkipTest('Requires a configured QGIS Python runtime') from exc
    app = QgsApplication.instance() or QgsApplication([], False)
    app.initQgis()
    canvas = QgsMapCanvas()
    planner = module.FlyPathDialog(SimpleNamespace(mapCanvas=lambda: canvas))
    mission = {'drone_model': module.registry.get('DJI Mini 3 Pro').website_code,
               'polygon': [[51, 13], [51, 13.001], [51.001, 13.001], [51.001, 13]],
               'settings': {'altitude': 80, 'direction': 95}}
    def snapshot():
        return (planner._website_settings(), planner._survey_polygon.asWkt(),
                planner._source_mode, copy.deepcopy(planner._website_link),
                copy.deepcopy(planner._waypoints), copy.deepcopy(planner._missions),
                list(planner._preview_layer_ids), sorted(QgsProject.instance().mapLayers()),
                canvas.extent().toString())
    try:
        planner._apply_website_mission(mission)
        planner._website_link = {'id': 17, 'revision': 2}
        before = snapshot()
        invalid = [dict(mission, settings={'altitude': 90, 'split_count': float('nan')}),
                   dict(mission, settings={'side_overlap': '70'}),
                   dict(mission, settings={'flight_path': 'unknown'}),
                   dict(mission, polygon=[[51, 13], [51.001, 13.001], [51, 13.001], [51.001, 13]]),
                   dict(mission, polygon=[[51, 13]] * 3),
                   dict(mission, polygon=[[91, 13], [51, 13.001], [51.001, 13]]),
                   dict(mission, waypoints=[[0, float('inf')]])]
        for bad in invalid:
            try:
                planner._apply_website_mission(bad)
            except module.FlypathSyncError:
                pass
            else:
                raise AssertionError('Invalid mission accepted')
            assert snapshot() == before, 'Rejected import mutated the planner'
        adjusted = planner._apply_website_mission(dict(mission, settings={
            'split_count': 1e300, 'split_max_wp': 1e300, 'side_overlap': -1e300}))
        assert adjusted
        assert planner.maxWaypointsSpin.value() == planner.maxWaypointsSpin.maximum()
        assert planner.sideOverlapSpin.value() == planner.sideOverlapSpin.minimum()
    finally:
        planner.close()
        canvas.close()
        app.processEvents()


if __name__ == '__main__':
    test_import_preserves_planner()
    print('Import validation and planner preservation passed')
