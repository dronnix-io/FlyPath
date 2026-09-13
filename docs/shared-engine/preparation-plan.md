# Shared engine delivery status

## Completed

- [x] Chose one pure Python engine used in-process by Django and vendored by
  the offline QGIS plugin.
- [x] Released shared 2D grid generation, automatic direction, route ordering,
  and WGS84 measurements in `v0.1.0`.
- [x] Released shared route-level time, photo, and battery estimates in
  `v0.2.0`.
- [x] Released shared versioned drone profiles in `v0.3.0`.
- [x] Connected both products to the shared geometry, measurements,
  statistics, and profiles.
- [x] Preserved saved website routes until explicit regeneration.

## Remaining, in order

1. Move the complete flight-splitting decision into the engine. The result
   must include contiguous flights, waypoint-limit enforcement, recovery
   distance, and per-flight plus mission totals.
2. Move capture/action decisions into the engine, including semi-auto start and
   restart behavior, full-auto photo waypoints, and photo-stop time.
3. Expose one versioned `plan_2d` request/result entry point with stable errors,
   warnings, assumptions, and all contract/profile/engine versions.
4. Replace client-side split, action, recovery, and estimate calculations with
   serialization of the shared result.
5. Complete reviewed parity fixtures and the supported QGIS/platform smoke
   matrix.

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

## Packaging check still required

Before removing the remaining client calculations, demonstrate installation
from the official QGIS repository, one offline calculation, and KMZ export on
Windows x64, Linux x64, macOS Intel, and macOS Apple Silicon. The plugin ZIP
vendors the released pure Python package and requires no compiler or dependency
installer.
