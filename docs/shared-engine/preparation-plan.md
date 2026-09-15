# Shared engine acceptance

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
