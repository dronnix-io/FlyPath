# Existing evidence

Website commit `1603e9f` aligned manual direction with plugin-generated QGIS
bearings and preserved legacy saved routes until explicit regeneration.

Verified during the website preparation work:

- 106 JavaScript tests pass.
- Manual 0, 70, 90, 180, 250, and 360 cases pass against the QGIS fixture.
- Auto preview, commit, save/reload, legacy preservation, and explicit
  regeneration are covered.

The reusable QGIS fixture is stored as
`tests/shared_engine/fixtures/plugin-direction.json`.

Since that initial evidence, both products have adopted the shared grid,
automatic direction, route ordering, WGS84 measurements, route-level
statistics, and drone profiles through engine `v0.3.0`. Unit and adapter tests
cover those seams, including the reported mission input.

Full-engine parity is not yet established. Flight-split policy, recovery,
per-flight totals, photo/action generation, and some export decisions still
live in client code. The fixture matrix also lacks independently reviewed
expected output for every contract case and the full QGIS/platform smoke run.
