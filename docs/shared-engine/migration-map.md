# Migration map

## First phase — local v0.4.0 candidate

The bundled engine is pinned to local tag `v0.4.0`, source commit
`050d368a59d2be903605099fe3cd006e917507e9`. The tag has not been published
remotely. Engine changes must still go through release/pin/vendor tooling.

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

QGIS 3.44.14 / Python 3.12 on Windows was tested. The candidate metadata is
restricted to the QGIS 3.44 line; previous 3.16/4.x and Python 3.9 claims no
longer apply to this candidate. The engine itself requires Python 3.10+.

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
