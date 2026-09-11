# Website sync security implementation

Specification: [website-sync-security-spec.md](website-sync-security-spec.md).

## Plugin

- The existing sync interface uses the encrypted QGIS authentication manager. Ordinary settings hold a reference and origin; the token and its origin binding live together in the encrypted payload. There is no plaintext fallback.
- Default-site legacy tokens migrate only after a successful encrypted write and read-back. Custom-site legacy tokens need explicit reconnection. Disconnect remains available for locked storage and failed migration, and removes local credentials without revoking server access. Rotate old tokens to invalidate backups.
- The destination must be a canonical HTTPS origin. Authenticated redirects are refused. Successful responses are limited to 2 MiB, error responses to 64 KiB, and JSON nesting to 32 levels. Non-finite values and malformed mission data are rejected before planner changes.
- The token prompt identifies the destination; origin and credential changes during UI event processing cancel the request. The current website exposes token generation and revocation under **Plugin token** in the account menu.
- CI scans fetched Git history with checksum-verified Gitleaks 8.30.1. Release contents must also be scanned before distribution; see [SECURITY.md](../SECURITY.md).

## Verification

- Installed QGIS 3.44.14: 121 plugin tests and 40 parameterized cases passed using an isolated profile.
- Separate real-QGIS credential process: all 15 tests passed, including encrypted persistence, migration, locked storage, failure recovery, origin binding and Disconnect.
- Pyflakes, Bandit, Qt enum checks and diff whitespace checks passed. Plugin Git history (234 commits at scan time) and tracked release contents had no candidate secrets.
- Independent standards and plugin-spec reviews found no actionable issues in the integrated security implementation. Later copy changes align the connection guidance with the upstream website's token dialog.
- Qt6 syntax compatibility was checked, but QGIS 4 and older supported QGIS versions were not runtime-tested here.

The existing Python-only CI command remains `python -m pytest -q tests`. QGIS-only checks skip when that runtime is unavailable. Run the credential test separately from other QGIS tests:

```powershell
& 'C:/Program Files/QGIS 3.44.14/bin/python-qgis-ltr.bat' tests/test_flypath_credentials.py
```

Use a disposable QGIS profile and settings directory for the other QGIS smoke tests; do not run them against a live planning session.

## Companion website and publication

The website is closed source; the plugin is open source. Website implementation files remain in the website's separate repository and are not tracked by this plugin repository. The changes cover their authentication and mission-sync interface.

The backend changes are on the companion website's `codex/website-sync-security` branch, based on `dev`. The reviewed checkout is outside the public plugin folder, beside the private website repository, and its original checkout was not edited by this task. Its private implementation report records server controls and operational limitations. Temporary plugin implementation worktrees were removed after their commits were merged; their branches remain recoverable in Git.

Final backend checks passed: 223 targeted Django tests, 272 full Django tests, and 105 JavaScript tests. Review found and corrected three issues: cache-window expiry, invalid survey geometry accepted by the plugin API, and implicit form submission triggering token revocation. Focused follow-up reviews confirmed all three fixes. Browser partial-draft behavior remains unchanged.

Website Git history and tracked-content scans found no credential leaks after investigating one prose-only false positive. The exact historical finding is excluded; current prose was reworded. Application tests cannot verify deployed proxy logs or enforce the checked-in GitHub workflow on Bitbucket. Production deployment/configuration remains a separate step.

Both branches remain local. GitHub denied connector issue creation and direct branch push (HTTP 403). Automatic approval review rejected the companion Bitbucket push because destination-specific publication authorization was not established. No website source was published to the plugin repository, and no production deployment or live credential revocation was performed.
