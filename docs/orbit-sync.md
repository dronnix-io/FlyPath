# Orbit mission integration

Orbit geometry, waypoint headings, camera actions, overlap, estimates, and
export limits come from engine `plan_orbit`, released in v1.2.0.
Consumers translate controls and serialize the engine result without
replanning the ring. Map circles and editing handles are consumer UI.

The mission-sync representation adds `settings.mapping_style: "orbit"`.
For this style, `polygon` contains exactly one `[latitude, longitude]` pair:
the orbit centre. Settings carry `orbit_radius` in metres (5–2,000),
`altitude` in metres (10–500), `orbit_tilt` in degrees (-90–0), and
`reverse_route` for counterclockwise travel. For multiple rings, optional
`orbit_rings` contains 1–3 ordered objects with exactly `radius_m`,
`altitude_m`, and `gimbal_pitch_deg`. The legacy radius, altitude, and tilt
settings always describe the first ring. A single-ring mission omits
`orbit_rings` and retains the v1.2.0 request shape. Existing 2D polygons and
corridor lines keep their representations.

Orbit missions use a curved path without terrain follow or cross-hatching.
`split_enabled` and `split_count` describe the actual `planning_request.split`
intent, including when the UI split checkbox is on but the planned route fits
one flight. On import, a checked split preference with count 1 may also accompany
an unsplit request. `planning_request` and `planning_result` retain the public engine
contract, ordered flight slices, per-waypoint headings, and ring heights and
camera tilts. Consumer DJI Fly profiles are supported; enterprise mapping
export cannot represent this route. Saved routes retain their provenance until
explicit regeneration.

Engine v1.3.0 adds orbits of up to 3 rings, flown in order, and splits them
into flights. The plugin sends a split request only when the orbit needs it or
the pilot asks for more flights. The website must support this sync schema
before deploying a plugin that sends multi-ring or split orbits.
