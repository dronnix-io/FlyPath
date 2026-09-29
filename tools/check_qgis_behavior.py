"""Run required QGIS regressions, failing if any test is skipped."""

import argparse
import os
from pathlib import Path
import sys

import pytest


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin('terminalreporter')
    if reporter.stats.get('skipped'):
        reporter.write_sep('=', 'Required QGIS tests were skipped')
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', action='store_true',
                        help='Run credential tests in their own process')
    args = parser.parse_args()
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    # Fail immediately if this is not a configured QGIS runtime.
    from qgis.core import Qgis, QgsApplication

    print(f'QGIS {Qgis.QGIS_VERSION}', flush=True)
    tests = Path(__file__).resolve().parents[1] / 'tests'
    names = ['test_flypath_credentials.py'] if args.vault else [
        'test_planning_dialog.py',
        'test_website_import_validation.py',
        'test_flypath_linked_save.py',
        'test_map_overlays.py',
        'test_preview_layers.py',
        'test_website_settings.py',
        'test_website_map_extent.py',
    ]
    # Keep the application alive across modules; the vault suite owns its own.
    app = None if args.vault else QgsApplication([], False)
    if app is not None:
        app.initQgis()
    try:
        return pytest.main(
            ['-q', '-p', 'no:cacheprovider', *(str(tests / name) for name in names)],
            plugins=[sys.modules[__name__]],
        )
    finally:
        if app is not None:
            app.exitQgis()


if __name__ == '__main__':
    sys.exit(main())
