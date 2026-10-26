# Oblique mission sync

Oblique missions use the existing version 1 mission-sync envelope and planning
contract version 1. Engine 1.4.0 adds the `plan_oblique` operation; earlier
servers and plugins reject unsupported missions without changing saved routes.

The mission uses `settings.mapping_style: "oblique"`, a polygon survey area,
and the ordinary grid, capture, reverse-route and flight-splitting settings.
Three additional settings are required:

| Setting | Values |
| --- | --- |
| `oblique_pattern` | `fast` or `full`; new missions default to `fast` |
| `oblique_pitch` | Camera pitch in degrees, -85 to -20; default -45 |
| `oblique_nadir` | Boolean; default true |

These map to the engine request's `pattern`, `gimbal_pitch_deg`, and
`include_nadir`. Both consumers preserve the saved request and result, including
each pass's flight boundaries, camera heading and pitch. Regeneration is explicit.

Website sync supports consumer aircraft and missions without terrain follow.
Cross-hatch is unsupported. Terrain-enabled plugin missions must be exported
locally, or terrain follow must be disabled and the route regenerated before
sync. A flat engine result must never be presented as the terrain-adjusted route.

Each flight belongs to one pass. Nadir and Fast passes follow the flight line;
Full passes hold their engine-supplied heading. Every flight exports separately,
with its own camera actions. Shared-engine validation and the mission settings
must agree before a saved route can be exported.

Engine 1.4.1 corrects narrow-area grids and Fast pass geometry. Saved 1.4.0 Fast
routes are verified using their original geometry and remain visible unchanged,
but require explicit regeneration before export or sync from the plugin.
