# One shared Python planning core

The website and QGIS plugin will use one pure Python planning implementation
to prevent route and calculation differences. The Django application imports
the package in-process. The QGIS plugin bundles the same released Python source
so planning and KMZ export work offline.

A separate calculation service, Rust binaries, browser Python, and permanent
independent Python/JavaScript engines are rejected. The browser sends planning
inputs to the existing Django application and renders its returned result.

The package must not depend on QGIS, Django, browser state, persistence, or KMZ
serialization. It has a versioned JSON-compatible contract and versioned drone
profiles. The core has its own repository and releases; Django installs a
release and the QGIS ZIP vendors that exact release as source.

Migration is phased. The first phase covers 2D planning without terrain:
direction, route generation, splitting, photo actions, and statistics. Existing
website and plugin engines remain only while a feature has not migrated. Saved
routes remain unchanged until explicit regeneration.

The shared core owns coordinate normalization, route geometry and ordering,
flight splits, photo actions, stopping times, measurements, and estimates.
Website and plugin adapters own UI, map drawing, persistence, sync, and KMZ XML
serialization. Exporters serialize core decisions without recalculating them.

The QGIS package must install without a terminal, compiler, or dependency
installer on Windows x64, Linux x64, macOS Intel, and macOS Apple Silicon.
Pure Python source avoids the official repository's native-binary exception,
but installation and operation still require tests on the supported QGIS and
Python versions.

The website requires a network request to Django for new calculations. The
plugin remains fully offline after installation. Requests are debounced in the
browser so editing does not create a request for every intermediate value.

Calculation rules use WGS84 ellipsoidal measurements, explicit recovery travel,
capture-based photo counts, and named time allowances. Sync rejects unsupported
engine or contract versions and asks the user to update. Updating software never
regenerates a saved route.
