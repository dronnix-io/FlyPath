# Shared engine preparation

## Deliverables before implementation

1. Agree on the versioned input/output contract.
2. Build reference fixtures for the first-phase 2D scope.
3. Record independent truth for area and distance; do not snapshot known bugs.
4. Separate intended plugin compatibility from behavior that needs correction.
5. Prove the pure Python package imports and runs in supported QGIS versions.

## Fixture matrix

- rectangle at manual 0, 70, and 90 degrees
- reported wastewater-site polygon at manual 70 degrees
- automatic direction on rectangular and irregular polygons
- concave polygon, polygon with a hole, and antimeridian-safe input
- zero and positive margin
- semi-auto and full-auto capture
- return-to-home and non-return finish actions
- one flight, requested split, and waypoint-limit forced split
- invalid ring, coordinate, version, and profile

Every fixture stores its input, expected output, provenance, and tolerances.
Expected values are reviewed data, never automatically refreshed snapshots.

## Acceptance

- Website and plugin receive byte-equivalent results from the same engine and
  profile versions.
- Area and distance match independent references within documented tolerances.
- Route bearings match the resolved direction.
- Flights respect waypoint limits and preserve coverage at seams.
- Full-auto photo count equals generated photo actions.
- Saved routes remain unchanged until explicit regeneration.

## Packaging check

Before route migration, demonstrate installation from the official QGIS
repository, one offline calculation, and KMZ export on Windows x64, Linux x64,
macOS Intel, and macOS Apple Silicon. The plugin ZIP vendors the released pure
Python package and requires no compiler or dependency installer.
