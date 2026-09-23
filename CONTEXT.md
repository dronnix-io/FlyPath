# FlyPath

FlyPath plans drone survey missions. This language is shared by the website,
QGIS plugin, and planning engine.

## Language

**Mission**:
One editable survey definition and its most recently generated route.
_Avoid_: Project, job

**Survey area**:
The geographic boundary to be covered. It is a polygon for 2D mapping and a
centre line plus width for corridor mapping.
_Avoid_: Field, plot

**Route**:
The ordered sequence the aircraft is expected to fly, generated from a survey
area, drone profile, and mission settings.
_Avoid_: Path, track

**Flight**:
One independently executable portion of a route. A route may contain multiple
flights because of battery or waypoint limits.
_Avoid_: Part, leg, sub-mission

**Waypoint**:
A geographic position on a route where the aircraft turns, captures, changes
height, or performs another action.

**Drone profile**:
A versioned set of aircraft and camera capabilities used to generate a route.
_Avoid_: Drone defaults, aircraft settings

**Engine version**:
The planning-engine release that generated a route. It identifies behavior;
installing another version does not alter an existing route.

**Regeneration**:
Explicitly replacing a mission's saved route using its current settings and an
available planning-engine version.
_Avoid_: Update, recalculate
