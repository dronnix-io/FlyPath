# Existing evidence

Website commit `1603e9f` aligned manual direction with plugin-generated QGIS
bearings and preserved legacy saved routes until explicit regeneration.

Verified on the website preparation worktree:

- 105 JavaScript tests pass.
- Manual 0, 70, 90, 180, 250, and 360 cases pass against the QGIS fixture.
- Auto preview, commit, save/reload, legacy preservation, and explicit
  regeneration are covered.

The reusable QGIS fixture is stored as
`tests/shared_engine/fixtures/plugin-direction.json`.

This evidence establishes angle compatibility, not full-engine parity.
Remaining known differences include scan-line placement, projection, Auto
candidate selection, area, distance, time, and photo estimates. The reported
mission's exact route has not yet been independently validated as correct.
