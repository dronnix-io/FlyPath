# Migration map

## First phase — v0.4.0

The bundled engine is pinned to released tag `v1.0.0`, source commit
`ba32f5be4d417eceb462e3edc98239232f6d5496`. Engine changes must still go
through release/pin/vendor tooling.

The v1.0.0 release retains v0.4.0 planning behavior and contract version 1.
Saved v0.4.0 results remain supported without changing their provenance.
The generated engine bundle includes its Apache-2.0 LICENSE and NOTICE.

| Capability | Owner | Status |
| --- | --- | --- |
| 2D geometry, ordering, direction, WGS84 measurement | Engine | Integrated |
| Versioned aircraft and camera values | Engine profiles | Integrated; consumer-local values are not planning authority |
| Complete split policy and per-flight results | Engine plan_2d | Integrated; splitting defaults on and can be disabled |
| Capture actions and full-auto action totals | Engine plan_2d | Integrated into consumer WPML serialization |
| Known outbound/recovery and incomplete totals | Engine plan_2d | Integrated; unknown travel remains explicit |
| Versioned result, warnings, limits, errors | Engine plan_2d | Integrated; unsupported saved results are view-only |
| One current preview/statistics/export result | Plugin adapter | Integrated for 2D without terrain |
| Saved-route preservation | Consumer adapters | Explicit Preview/regeneration required after imported-route edits |
| Terrain/corridor defect fixes | Plugin legacy paths | Cached route invalidation, shared heights, segment sampling, failed-terrain export blocking |

The website `/api/plan/` endpoint is a website adapter, not a service required by
the offline plugin. Both consumer adapters and KMZ actions are compared by
`flypath_engine/tools/check_consumer_parity.py`.

## Verification and limits

QGIS 3.44.14 / Python 3.12 on Windows was tested, and the plugin was verified
to load on QGIS 3.34 and 4.0.3. The release metadata supports QGIS 3.34 through
4.99; the previous QGIS 3.16 and Python 3.9 claims no longer apply. The engine
itself requires Python 3.10+.

Twenty-one of 22 direct plugin test scripts passed. The unchanged credential
vault suite stalled and is not counted as passed. Focused final adapter,
dialog, terrain, and WPML checks pass. The built ZIP was extracted and used
offline to plan/export three flights with 177 photo actions; plugin-manager
installation and aircraft/controller execution remain unverified.

Enterprise shared-result export is explicitly blocked because the native
mapping writer cannot yet serialize the complete action contract. Syncing
polygon holes or multipart areas is blocked until the public mission-sync
contract can represent them losslessly. Imported terrain routes require Preview
to reacquire heights.

## Later phases

- Launch/home picker UI (coordinates are already supported by the contract)
- Corridor and terrain migration into the engine
- Cross-platform controller delivery
- Broad UI simplification after these ownership boundaries are validated
