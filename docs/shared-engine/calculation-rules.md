# Calculation rules

These rules define intended behavior. Exact library selection and numeric
tolerances remain reviewable until the packaging decision is resolved.

## Area and distance

- Measure polygon area and route segments on the WGS84 ellipsoid.
- Report survey area separately from camera coverage.
- Report route distance, recovery distance, and their total separately.
- Add recovery only when the finish action flies it and its destination is
  known. A first waypoint is not silently treated as home.

## Time

Estimated flight time is the sum of travel time, generated photo-stop time,
per-flight startup actions, and named operational allowances. Every allowance
is returned in the calculation assumptions. A blanket multiplier is accepted
only after flight-log validation and must not duplicate explicit delays.

## Photos

- Semi-auto counts captures only over route portions where interval capture is
  enabled. First-trigger, turns, connectors, recovery, and restart behavior must
  be explicit.
- Full-auto photo count equals generated photo actions.
- Photo estimates never increase merely because a travel-time safety allowance
  increased.

## Initial acceptance tolerances

- Direction interpretation: 0.01 degree at the engine boundary.
- Geographic strip bearing: 0.05 degree against an independent reference.
- Route waypoint position: 0.20 metre against an approved reference route.
- Distance: greater of 0.05 metre or one part per million.
- Area: greater of 1 square metre or one part per million.

Both products consume the same shared-core output, so product-to-product values must
be identical before display formatting.
