# Repository and CI preparation

The shared engine has its own repository and versioned releases.

```text
flypath-engine/
  src/flypath_engine/  pure planning logic
  profiles/            versioned drone profiles
  fixtures/            reviewed compatibility cases
  schemas/             request and result contracts
```

Django installs a pinned release. The QGIS build copies that same release into
the plugin ZIP. The geometry implementation uses Shapely and pyproj; Django
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

Initial shared release: `flypath-engine==0.1.0` (Git tag `v0.1.0`).
