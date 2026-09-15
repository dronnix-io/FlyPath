# Decisions

## Resolved

1. Use one pure Python engine. Django imports it and the QGIS plugin vendors the
   same tagged source for offline, cross-platform use.
2. Use WGS84 ellipsoidal area and distance with the tolerances in
   `calculation-rules.md`.
3. Keep drone profiles in the engine and release corrections through a new
   engine tag. Both clients pin the same tag.
4. Preserve saved routes across software updates; regeneration is explicit.
5. Reject unsupported synced engine/contract versions and ask the user to
   update. Support a small, explicitly tested compatibility range.

## Still required before the first phase is complete

1. Publish the exact QGIS/Python/platform support matrix. Current plugin
   metadata claims QGIS 3.16+ and Python 3.9+, while the engine package declares
   Python 3.10+; that mismatch must be resolved from tested installations.
2. Define startup, stopping, turn, recovery, and other flight-time allowances
   and validate their values.
3. Define semi-auto capture on starts, split restarts, turns, connectors, and
   recovery travel.
4. Freeze route start-corner and deterministic tie-breaking rules in fixtures.
5. Decide whether curved turns change calculated geometry or remain a renderer
   and exporter hint.
6. Define the exact supported engine/contract compatibility table and error
   codes.
