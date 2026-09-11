# Security

FlyPath is public source software. The website must authorize every request independently of the plugin. Never embed a shared service secret or administrator credential in the plugin.

## Tokens and mission data

Treat plugin tokens as passwords and mission geometry as private account data. Never include real tokens in issues, screenshots, logs, test fixtures, exported missions, or QGIS projects. Use synthetic credentials and coordinates when reporting problems.

Disconnect forgets this device's credential; it does not revoke server access. Revoke or regenerate a token on the website if a device is lost or a credential may have been copied. Migrating a legacy settings token cannot erase copies in backups: rotate that token to invalidate those copies.

## Before release

- Run the repository's CI checks and the supported-QGIS credential and mission import checks described in the security implementation report.
- Run Gitleaks against Git history and the actual staged release contents, with `--redact`. CI scans full fetched history using a versioned, checksum-verified Gitleaks binary. Scan release contents locally with `gitleaks dir --redact <release-directory>` before distributing an archive.
- Treat scanner findings as candidates to investigate, not automatic proof of compromise or automatic false positives. Revoke or rotate any exposed real credential before removing it. Do not post a finding's secret value publicly.
- Confirm the companion website protections and migrations are deployed before describing the combined connection as hardened. Passing plugin tests alone does not establish server authorization or rate limiting.

These controls protect storage and transit; an unlocked machine running malicious code as the user can still expose credentials in memory.
