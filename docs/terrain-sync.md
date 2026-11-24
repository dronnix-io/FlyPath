# Terrain and takeoff sync

Mission sync carries the takeoff settings that terrain follow needs, so a
mission opened from flypath.io exports the same heights as the one saved.

## Launch Offset

For 2D, Corridor, Oblique, and terrain-following Orbit missions, sync carries
**Launch Offset** as `settings.launch_offset_m` (metres, -50 to 50). Older
missions without the field use zero. The plugin sends zero explicitly when
clearing a saved offset. Positive values mean the takeoff point is higher than
the reference ground, so export subtracts that offset from waypoint heights.
Flat Orbit and Panorama missions send zero; Panorama terrain-following missions
remain local. The plugin advertises `launch-offset-v1` in
`X-FlyPath-Sync-Capabilities` so the website can prevent older plugins from
opening or changing missions with a nonzero offset.

## Same takeoff for all splits

Sync carries **Same takeoff for all splits** as `settings.takeoff_reference`:
`shared` uses the ground elevation at the first waypoint of the full flown
route (after reversal) for every split, while `per_flight` uses the ground
elevation at each flight's first waypoint. Older website missions without this
field use `per_flight`; new local plugin missions still default to `shared`.
The setting is sent for every syncable mission type, though it has no effect
while terrain follow is off. The plugin advertises `takeoff-reference-v1` in
`X-FlyPath-Sync-Capabilities` so the website can guard terrain-following
missions against clients that cannot preserve this choice.

## Orbit terrain follow

Terrain-following Orbit missions sync with their saved route,
`settings.terrain_follow`, launch offset, and takeoff reference. The plugin
advertises `orbit-terrain-v1` in `X-FlyPath-Sync-Capabilities` so the website
can guard these missions against older clients. Local export still needs an
available elevation source to calculate terrain heights.
