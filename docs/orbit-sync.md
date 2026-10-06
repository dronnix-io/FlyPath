# Orbit mission integration

Orbit geometry, waypoint headings, camera actions, overlap, estimates, and
export limits come from engine `plan_orbit`, released in v1.2.0.
Consumers translate controls and serialize the engine result without
replanning the ring. Map circles and editing handles are consumer UI.

The mission-sync representation adds `settings.mapping_style: "orbit"`.
For this style, `polygon` contains exactly one `[latitude, longitude]` pair:
the orbit centre. Settings carry `orbit_radius` in metres (5–2,000),
`orbit_tilt` in degrees (-90–0), and `reverse_route` for counterclockwise
travel. `planning_request` and `planning_result` retain the public engine
contract and per-waypoint headings. Existing 2D polygons and corridor lines
keep their representations.

Orbit missions use a curved path and one flight, without terrain follow,
cross-hatching, or mission splitting. Consumer DJI Fly profiles are supported;
enterprise mapping export cannot represent this route. Saved routes retain
their provenance until explicit regeneration.
