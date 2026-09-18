# Compatibility evidence

The reusable direction fixture is stored at
`tests/shared_engine/fixtures/plugin-direction.json`. It covers representative
manual bearings and automatic direction behavior against QGIS-generated output.

Release `v0.4.0` adds public-boundary parity checks for eight
semi/full-auto, manual/automatic, and cross-hatch combinations. Both consumers
produce equivalent planning results, split-flight coordinates, and supported
camera actions within the documented serialization tolerance.

Engine unit tests cover geometry, WGS84 measurements, route ordering,
statistics, profiles, time-balanced splitting, waypoint limits, and structured
validation. Plugin tests cover the adapter, saved-route preservation, terrain
guards, and supported KMZ output.

Remaining evidence is listed in `migration-map.md`: broader QGIS/platform
installation coverage and controller or aircraft execution testing are still
required.
