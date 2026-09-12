# Shared planning engine contract

This contract defines the boundary used by the FlyPath website and QGIS plugin.
It describes values and behavior independently of Python, JavaScript,
QGIS, map rendering, persistence, or KMZ serialization.

## Versioning

Every request contains `contract_version`. Every result contains
`contract_version`, `engine_version`, and `profile_version`.

- Additive optional fields may keep the contract version.
- Changed meaning, units, defaults, ordering, or required fields increments it.
- Mission sync rejects unsupported versions and asks the user to update.
- Updating software never regenerates a saved route. Regeneration is explicit.

## Coordinates and numbers

- Positions use objects: `{ "latitude_deg": 51, "longitude_deg": -114 }`.
- Latitude is -90 through 90; longitude is -180 through 180.
- Arrays never rely on ambiguous `[x, y]` coordinate ordering.
- Distance uses metres, time seconds, angles degrees, and area square metres.
- Direction uses the plugin's established convention: counterclockwise from
  local grid North, normalized to `[0, 180)` for bidirectional survey lines.
  Zero is north-south; 90 is east-west. Requests and results name this
  convention explicitly; geographic strip bearings remain separate outputs.
- Inputs and outputs are finite JSON numbers. NaN and infinity are rejected.
- Identical versioned inputs produce deterministically ordered outputs.

## First-phase request

The `plan_2d` request contains:

- contract and drone-profile versions
- survey exterior ring and optional interior rings
- altitude, speed, side overlap, and margin
- manual direction with an explicit convention, or automatic direction mode
- semi-auto interval or full-auto capture behavior
- turn style and finish action
- split enablement, requested flight count, and waypoint limit

Corridor planning and terrain following are outside the first phase.

## First-phase result

The result contains:

- resolved direction and ordered survey strips
- ordered route waypoints with explicit roles
- flights referencing contiguous route portions
- photo actions attached to route positions
- route, recovery, and total estimated flight distance
- survey area, flight time, photo count, strip count, waypoint count, and
  battery count
- calculation assumptions
- warnings with stable codes and readable messages

## Ownership

The shared Python core owns coordinate normalization, direction, 2D route generation, ordering,
splitting, photo actions, geographic measurements, estimates, and planning
warnings.

The website and plugin own UI state, map drawing, persistence, sync,
authentication, and KMZ serialization. KMZ writers serialize core decisions;
they do not recalculate routes, splits, photo actions, or estimates.

## Errors

Errors contain a stable code, field path where applicable, and display message.
Invalid geometry, unsupported versions, missing profiles, and invalid values
fail explicitly. A partial flyable route is never returned as success.
