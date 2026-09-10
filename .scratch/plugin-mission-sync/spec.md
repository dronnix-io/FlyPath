Status: ready-for-agent

# Plugin -> website mission sync (token push/pull)

## Problem Statement

A pilot plans missions in two disconnected places: the FlyPath QGIS plugin
(offline, no account, exports WPML KMZ only) and the FlyPath website (an
account, a dashboard of missions, review links). Moving a mission between the
two today means re-drawing it by hand or shuttling a KMZ file manually — KMZ
is an export format, not a re-editable mission, so nothing comes back into
either tool as a mission the pilot can keep working on.

The website side of this is already built and shipped (see
`flypath.io` repo, `.scratch/plugin-mission-sync/spec.md` and
`tests/test_plugin_sync.py`): a personal access token, a profile page to
generate/regenerate it, and three token-authenticated endpoints
(`POST/GET api/missions/`, `GET api/missions/<id>/`). This spec covers only
the plugin side that talks to that already-live API.

## Solution

The plugin holds a personal access token (pasted once from the website's
profile page) and uses it to push a mission it just planned straight into
the pilot's website account as a normal draft mission, and to pull a mission
back down from the account to keep planning it in QGIS. No login flow in the
plugin, no new mission-sharing concept — the token just authenticates the
plugin for the same actions a browser session already performs.

## User Stories

1. As a plugin user, I want to paste my website token into the plugin once,
   so that every mission after that is a one-click send, not a re-auth each
   time.
2. As a plugin user, I want the plugin to tell me clearly if I haven't set a
   token yet when I try to send or load a mission, so that I know what to do
   instead of seeing a confusing failure.
3. As a plugin user, I want my token stored only on my machine (QGIS
   settings), never logged or transmitted anywhere but the API request
   header, so that it can't leak into logs, crash reports, or the project
   file.
4. As a plugin user, I want a "Send to FlyPath" action after I preview a
   mission, so that it lands in my website dashboard without leaving QGIS.
5. As a plugin user, I want the send action disabled or hidden until a
   mission has been previewed, so that I can't push an incomplete or
   unvalidated plan.
6. As a plugin user, I want to see confirmation after a successful send,
   with a link I can open to the mission on the website, so that I know it
   worked and can jump straight to it.
7. As a plugin user, I want a clear, specific error if the send fails (no
   token set, invalid/revoked token, no network, server validation
   rejection), so that I'm not left wondering if it worked or why it didn't.
8. As a plugin user, sending a mission always creates a new draft on the
   website, so that I never accidentally overwrite a mission that already
   exists there.
9. As a plugin user, I want a "Load from FlyPath" action that lists my
   website missions by name and last-updated date, so that I can pick one to
   bring into QGIS without opening a browser.
10. As a plugin user, I want the mission list to show a friendly empty state
    if I have no missions on the website yet, so that I'm not confused by a
    blank picker.
11. As a plugin user, choosing a mission from the list loads its full plan
    (survey area, drone, settings) into the plugin as a new local mission, so
    that I can keep planning it there.
12. As a plugin user, loading a mission from the website makes an
    independent local copy — it never links back to or overwrites the
    website mission, and sending it again creates a separate new draft — so
    that the plugin and website can't silently clobber each other (mirrors
    the site's own Fork vs Duplicate distinction, see `flypath.io`
    `CONTEXT.md`).
13. As a plugin user, I want the plugin to tell me plainly when a website
    mission uses a mapping style or setting the plugin doesn't support (e.g.
    a drone the plugin doesn't have), so that a pull fails with a clear
    reason instead of silently producing a broken local mission.
14. As a plugin user, I want to tell the plugin doesn't recognize a survey
    area type (something other than 2D or corridor) it can't yet plan, so
    the load is refused rather than misinterpreted.
15. As a plugin user, network calls to the website (send/load) should never
    block the QGIS UI from becoming unresponsive for longer than a normal
    dialog wait, so that a slow or dead connection doesn't look like a
    crash.
16. As a plugin developer, I want the network/API logic isolated in one
    module with no QGIS or Qt-widget dependency, so that it can be unit
    tested the same way the rest of the plugin's pure-Python logic already
    is (`tests/`, pytest, no QGIS runtime required).
17. As a plugin developer, I want the drone-name-to-website-code mapping to
    live in the existing single source of truth for drone data
    (`hardware/drones.json` via `hardware/registry.py`), so that adding a
    new supported drone in one place keeps both the KMZ export and the
    website sync in sync automatically.

## Implementation Decisions

- New module, e.g. `flypath_sync.py`, at the plugin root alongside
  `flypath_dialog.py`. Pure Python (stdlib `urllib.request`/`json` only, the
  same approach already used in `terrain.py` for the AWS Terrarium DEM
  calls and mirrored on the website's own `website/qgis_stats.py`) — no new
  third-party HTTP dependency. Exposes three functions against the existing
  live API:
  - `push_mission(base_url, token, payload) -> dict` — POST
    `api/missions/`. `payload` shape matches what the website already
    accepts: `name`, `drone_model` (website code), `polygon`, `waypoints`,
    `settings`, `estimates`. Never sends an `id` (a push always creates).
  - `list_missions(base_url, token) -> list[dict]` — GET `api/missions/`,
    returns `{id, name, updated_at}` entries.
  - `get_mission(base_url, token, mission_id) -> dict` — GET
    `api/missions/<id>/`, returns the full payload (`polygon`, `waypoints`,
    `settings`, `estimates`, `drone_model`, `name`).
  - All three send `Authorization: Token <value>` as a request header, never
    in a query string or body. A non-2xx response raises a single
    `FlypathSyncError(message)` the dialog layer catches and shows as a
    message box; the module does not know about Qt.
  - `base_url` defaults to the production site but is overridable (keeps
    a staging/local target possible without a code change).
- Token storage: `QSettings`, keyed under the plugin's existing settings
  namespace. Read/write helpers live in `flypath_sync.py` (or a small
  sibling) so the dialog code never touches `QSettings` directly for this.
  The token is never written to the project file or any exported artifact.
- Drone mapping: add a `"website_code"` key to each **consumer** entry in
  `hardware/drones.json` (the plugin doesn't support the enterprise
  Matrice 4E the website also lists, so it needs no code). Values match the
  live `DroneConfig.code` slugs already seeded on the website: `mini3pro`,
  `mini4pro`, `mini5pro`, `air3`, `air3s`, `mavic3classic`, `mavic4pro`.
  `hardware/registry.py` exposes it alongside the existing drone fields so
  both `flypath_sync.py` (push/pull) and any future consumer read it from
  the one place.
- Settings mapping: the plugin's internal mission parameters (altitude,
  overlaps, speed, direction, margin, capture mode, flight path, terrain
  follow, cross-hatch, split count/max waypoints, finish/RC-lost action,
  mapping style) map onto the website's `settings` dict keys 1:1 by name
  where they already match; any plugin-only concept the website's
  `mission_settings.py` whitelist doesn't recognize (e.g. takeoff-zone
  tolerance, DEM raster choice) is simply omitted from the pushed
  `settings`, not invented as a new server-side key — this is a plugin-side
  filtering decision only, no website change.
- Push flow (UI): a "Send to FlyPath" button appears in the export/action
  area once a mission has been previewed (same gating as the existing
  Export button). On click: build the payload from the current preview
  state and drone selection (via the `website_code` mapping — refuse with a
  clear message if the selected drone has none), call `push_mission()`,
  show a success message with the website mission URL or a specific error.
- Pull flow (UI): a "Load from FlyPath" entry (menu item or button) opens a
  small picker dialog listing `list_missions()` results (name +
  last-updated); choosing one calls `get_mission()` and builds a new local
  survey area/mission from the returned payload — same construction path
  the plugin already uses when a user draws or imports an area, not a
  separate code path. A mapping style or drone code the plugin can't
  resolve aborts the load with a specific message before any map state
  changes.
- Both network calls run synchronously from the button's click handler for
  v1 (matches the plugin's existing terrain-follow DEM fetches, which are
  also synchronous); a spinner/disabled-button state during the call is
  enough to satisfy the "don't look frozen/crashed" story — a background
  thread is not required unless real-world latency proves it necessary.

## Testing Decisions

- `flypath_sync.py` is pure Python with no QGIS import, so it's tested the
  same way as the existing `tests/test_wpml_writers.py`,
  `tests/test_hardware_registry.py`, etc.: plain `test_*.py` functions,
  runnable directly or via pytest, no QGIS runtime.
- Mock at the HTTP boundary (`urllib.request.urlopen`), not at
  `push_mission`/`list_missions`/`get_mission` themselves — assert on the
  request that was built (URL, method, `Authorization` header value, JSON
  body shape) and on how a mocked response is turned into the function's
  return value or a raised `FlypathSyncError`.
- Cases to cover: successful push/list/detail; server 401 (bad/missing
  token) raises with a message the UI can show as-is; server 400 (payload
  rejected, e.g. unknown drone code) raises with the server's error text
  preserved; network failure (connection error/timeout) raises a distinct,
  user-legible message; token is always sent as a header, never appended to
  the URL.
- `hardware/registry.py`'s existing validation tests extend to cover
  `website_code`: present and non-empty for every consumer drone, absent
  (or explicitly `None`) for entries the website doesn't support.
- Dialog/UI wiring (button state, picker dialog, message boxes) is not unit
  tested — matches the website spec's own stance and this plugin's existing
  practice of not testing QGIS widgets directly.

## Out of Scope

- Any website/server-side change — that API is already live and tested in
  the `flypath.io` repo.
- Two-way sync or conflict resolution. Pulling makes an independent local
  copy; editing it in the plugin never writes back to the website mission,
  and sending it again always creates a new draft.
- Scoped/limited tokens (read-only, single-mission). One token per user,
  full push/pull, matches the website's v1.
- OAuth or any in-plugin login flow.
- Background/async network calls, retry/backoff, or offline queueing.
- Linux/macOS-specific concerns beyond what the plugin already handles —
  this feature has no OS-specific surface.
- Editing or regenerating the token from inside the plugin (that stays a
  website-only action on the profile page; the plugin only stores and uses
  what's pasted in).

## Further Notes

- Mirrors, not replaces, the plugin's existing "Send to DJI RC" flow: both
  are "get this planned mission somewhere else" actions, but one targets a
  USB-connected controller and this one targets the pilot's own website
  account. Keep them visually and behaviorally distinct in the action bar
  so a user doesn't confuse "goes to my drone" with "goes to my dashboard."
- The website spec (`flypath.io/.scratch/plugin-mission-sync/spec.md`)
  already resolved the drone-mapping question this spec depends on by
  listing its live `DroneConfig.code` values; if the website ever adds a
  new consumer drone, `hardware/drones.json`'s `website_code` needs a
  matching update or push/pull for that drone silently has nothing to map
  to (push should refuse with a clear message rather than send a blank or
  guessed code).
