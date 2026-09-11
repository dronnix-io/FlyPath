# Website sync security implementation graph

Specification: [website-sync-security-spec.md](website-sync-security-spec.md)

| Ticket | Work | Blocked by | Status |
| --- | --- | --- | --- |
| SEC-1 | Protected QGIS credential storage, migration, origin binding and Disconnect | None | Complete |
| SEC-2 | HTTPS-only transport, redirect rejection, bounded responses and atomic import validation | None | Complete |
| SEC-3 | Website ownership, token lifecycle, validation, limits, safe audits and authenticated API tests | None | Complete |
| SEC-4 | Repository secret scanning and release/CI safeguards | None | Complete |
| SEC-5 | Integrate changes, regression tests, standards/spec review, and PR handoff | SEC-1, SEC-2, SEC-3, SEC-4 | Code and review complete; publication blocked |

SEC-1 and SEC-2 are independent plugin worktrees. SEC-3 is an isolated worktree of the companion website repository; its original checkout is left untouched. SEC-5 cannot be marked complete based on plugin tests alone.

External issue creation was attempted but GitHub's connector returned HTTP 403. These local tickets preserve the implementation graph without claiming external publication succeeded.

The plugin remains public source and the website remains private. No website code is tracked in this repository. Publication is not performed: GitHub needs a writable destination; automatic approval review requires explicit authorization for the private Bitbucket push. See [verification and handoff](website-sync-security-report.md).
