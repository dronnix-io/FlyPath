# Repository and CI preparation

The shared engine has its own repository and versioned releases.

```text
flypath-engine/
  src/flypath_engine/  pure planning logic
  profiles/            versioned drone profiles
  fixtures/            reviewed compatibility cases
  schemas/             request and result contracts
```

Django installs a pinned release. `python tools/vendor_engine.py` fetches the
tag and commit pinned in `flypath-engine.json`, validates them, and refreshes
the ignored copy included in the QGIS plugin ZIP. CI fetches the pinned release
before tests. `python tools/build_plugin.py` fetches and verifies that release
before creating the QGIS ZIP. Engine source is not committed in the plugin
repository. The geometry implementation uses Shapely and pyproj; Django
installs them normally, while each supported QGIS build must prove that its
bundled versions satisfy the engine's tested range.

Required checks:

- core unit and fixture tests on every change
- deterministic output across supported Python versions
- Django request/response integration test
- plugin installation, offline calculation, and KMZ smoke test on Windows x64,
  Linux x64, macOS Intel, and macOS Apple Silicon
- supported QGIS release matrix

Do not add a package registry, native extension, or separate deployment service
until a demonstrated requirement needs one.

Released slices:

- `v0.1.0`: 2D geometry, direction, ordering, split helpers, and measurements
- `v0.2.0`: route-level mission statistics
- `v0.3.0`: shared drone profiles

CI covers Linux x64 on Python 3.10 and 3.13, plus Windows x64 and macOS Apple
Silicon on Python 3.13. Add the statistics and profile tests to that workflow;
they currently run locally and through client tests but are omitted from the
engine workflow. The QGIS installation/KMZ smoke matrix, including macOS Intel,
also remains required before the old client calculations are removed.
