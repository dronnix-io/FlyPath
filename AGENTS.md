# FlyPath QGIS plugin

Visibility: **PUBLIC**. Every committed change must be safe for public disclosure.

## Scope

This Python/PyQGIS plugin plans drone survey missions and exports DJI WPML KMZ files. It vendors the released pure-Python planning engine and integrates with flypath.io only through intentionally public behavior.

Read `CONTEXT.md` for domain vocabulary. Read `docs/adr/0001-shared-planning-core.md` before changing planning ownership or engine integration. Follow `CONTRIBUTING.md` for the maintained check and packaging commands.

Important areas:

- `flypath.py`, `flypath_dialog.py`: plugin entry point and main panel.
- `planning_adapter.py`, `flypath_engine/`: adapter and vendored engine source.
- `flypath_sync.py`, `flypath_library.py`, `flypath_credentials.py`: public website-sync client and credential storage.
- `wpml/`: consumer/enterprise KMZ serialization.
- `hardware/`: drone registry.
- `tests/`: pure-Python and QGIS-adjacent tests.
- `tools/`: engine vendoring, checks, and ZIP packaging.

## Commands

CI installs its tools with:

```powershell
python -m pip install --upgrade pip pytest bandit pyflakes "pyproj>=3.7,<4" "shapely>=2.1,<3"
```

Run checks from this repository:

```powershell
python -m pyflakes $(git ls-files '*.py')
python tools/check_qt6_enums.py
python tools/vendor_engine.py --check
python -m pytest -q tests
```

Build the plugin ZIP with `python tools/build_plugin.py`. For development, copy or link the repository into the QGIS plugin directory and enable FlyPath in QGIS; no standalone run command is documented.

## Conventions and safety

Use `qgis.PyQt` imports and preserve Qt 5/Qt 6 compatibility. Keep planning calculations in the shared engine; plugin code owns QGIS UI, map state, sync, and KMZ serialization. Update `flypath-engine.json` and run `tools/vendor_engine.py`; do not hand-edit the vendored engine copy.

Normally leave generated or local artifacts alone: `dist/`, `__pycache__/`, `.pytest_cache/`, `.scratch/`, `symbology-style.db`, and built ZIP files. Do not expose tokens, local settings, private service URLs, or private website implementation details. Never log or persist plaintext plugin tokens.

Public interfaces with the other repositories are the versioned engine contract/profiles and the documented flypath.io mission-sync API. Coordinate contract changes with both consumers.

