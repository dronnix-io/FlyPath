# Secure FlyPath plugin and website mission sync

## Problem Statement

Pilots connect the public FlyPath QGIS plugin to their website account using a personal access token. They need confidence that this connection protects credentials and mission locations, prevents access to another pilot's missions, and cannot grant broader account privileges.

Public source code lets anyone reproduce or modify plugin requests. Website security must therefore hold independently of plugin checks. Inspection of the plugin found ordinary QSettings token storage, acceptance of HTTP and configurable destinations, default urllib redirect handling, and response reads without a size limit. The website implementation has not been inspected; server protections below are requirements to verify, not confirmed vulnerabilities.

## Solution

Harden the existing personal-token workflow while preserving My missions, Open mission, Save changes, and Save as new. Protect stored credentials, send them only to the intended HTTPS origin, enforce account ownership and bounded data at the API, and give pilots an effective way to revoke access. Keep the plugin public and retain the existing revision conflict behavior.

## User Stories

1. As a pilot, I want my plugin token stored in protected credential storage, so that ordinary settings do not expose account access.
2. As an existing pilot, I want a safe transition from the old token storage, so that upgrading does not leave a plaintext credential behind.
3. As a pilot, I want connections to require valid HTTPS, so that my credentials and mission locations are protected in transit.
4. As a pilot, I want my token bound to the website origin I connected to, so that changing a server address cannot send it elsewhere.
5. As a pilot, I want authenticated requests to reject redirects, so that a redirected request cannot leak credentials.
6. As a pilot, I want my mission library to contain only missions I may access, so that other accounts remain private.
7. As a pilot, I want mission reads and updates to check ownership, so that guessing an ID cannot expose or alter a plan.
8. As a pilot, I want plugin access limited to necessary mission operations, so that a stolen token cannot change billing, administration, or account security.
9. As a pilot, I want to revoke a token from my website profile, so that a lost device can immediately lose API access.
10. As a pilot, I want token expiration and rotation explained clearly, so that I can reconnect without losing local planning work.
11. As a pilot, I want Disconnect to explain that it forgets access on this device, so that I know when website revocation is also necessary.
12. As a pilot, I want malformed or oversized downloaded missions rejected before they change my plan, so that errors preserve my local work.
13. As a pilot, I want revision conflicts to preserve both versions, so that security changes do not weaken protection against overwrites.
14. As an operator, I want server validation to reject invalid fields and geometry even from modified clients, so that plugin-side checks cannot be bypassed to corrupt data.
15. As an operator, I want bounded requests, storage, and request rates, so that one client cannot consume disproportionate resources.
16. As an operator, I want useful audit events without tokens or full mission payloads, so that I can investigate abuse without creating a new data leak.
17. As a maintainer, I want reproducible tests for credential transport and account isolation, so that future changes do not reopen these risks.
18. As a maintainer, I want repository and release secret checks, so that public distribution does not expose real credentials.

## Implementation Decisions

- Preserve the existing personal access token design. Do not introduce a shared plugin secret or rely on source-code secrecy, CORS, hidden endpoints, or client validation for authorization.
- Put transport enforcement in the shared sync request layer used by list, get, create, and update operations. Require HTTPS and normal certificate verification. Reject authenticated redirects rather than following them. Parse and validate the origin, including scheme, hostname, and port; reject embedded URL credentials and malformed destinations.
- Bind each stored credential to its validated origin. Production defaults to FlyPath's existing website. A separate staging connection must use its own explicitly configured HTTPS origin and credential; never reuse the production token automatically. Tests may emulate network behavior without a production HTTP bypass.
- Prefer the existing QGIS authentication manager for protected persistence, subject to compatibility verification on supported QGIS versions. Store only a credential reference and origin in ordinary settings. If protected storage cannot be used or unlocked, offer session-only access or a clear reconnect error; never silently fall back to plaintext.
- Migrate an existing settings token only after protected storage succeeds and can be read back. Remove the legacy settings value after successful migration. On failure, do not claim migration succeeded; offer a clear path to forget the old token and reconnect. Explain that prior copies in backups require token rotation to invalidate.
- Disconnect removes the active locally stored credential, legacy settings value where present, and mission link. It does not imply server revocation. Website token revocation remains an explicit account action.
- Verify the website authenticates every plugin API request, derives the account from the token, filters mission lists by ownership, and applies ownership checks to individual reads and updates. Ignore or reject client-supplied ownership fields. Deny unauthenticated requests and cross-account access without returning private mission data.
- Restrict plugin tokens to mission listing, reading, creation, and updating as required by the current workflow. Do not add deletion, billing, administration, or account-management privileges.
- Verify or implement cryptographically random tokens, server-side token hash storage, expiry, revocation, and rotation. Show new token secrets only at issuance. Require authenticated account access and CSRF protection for browser-based token management. Determine the exact expiry policy after inspecting existing website conventions; it is not yet agreed.
- Enforce server-side schemas, allowed fields, finite numeric values, valid coordinate ranges, valid mission settings, point counts, and body-size limits. Preserve the existing 2,000-point contract unless backend inspection establishes a different current contract. Reject attempts to modify ownership or other protected fields.
- Bound successful and error response reads before JSON parsing. Validate imported mission shape, point count, finite coordinates, and supported settings before changing planner state. Set concrete byte and pagination limits against valid maximum-size mission examples and existing API behavior during implementation.
- Apply rate limits and account storage quotas on the website using existing infrastructure where available. Return actionable failures without dropping local edits. Specify numerical limits after inspecting existing usage and configuration.
- Log authentication failures, token lifecycle events, and mission mutation outcomes with safe account or credential identifiers. Redact authorization headers and token values throughout application, proxy, and error reporting systems. Avoid logging complete mission geometry by default.
- Preserve Save as new creating a separate mission, Save changes using revision checks, conflict handling that preserves both versions, and invalid credentials prompting reconnection.
- Inspect existing security automation before adding checks. Scan current tracked content and repository history for real secrets; any discovered secret needs revocation or rotation, not only removal from the latest source.
- Coordinate backend changes with the website repository once its implementation is identified. Do not declare server requirements satisfied based on plugin behavior alone.

## Testing Decisions

- Proposed primary seam: exercise the plugin's public list, get, create, and update operations using the existing pure-Python sync test approach. Assert observable results, outgoing destinations, credential handling, and preserved data rather than private helper structure.
- Add a focused local transport integration check for rejected redirects and HTTPS policy. A mocked successful response alone cannot prove how the underlying HTTP client handles redirects. Assert that a redirect target receives no credential-bearing request.
- Exercise the website through its authenticated HTTP API using two independent accounts. A token for account A must never list, read, or update account B's mission, including guessed IDs and supplied ownership fields. Missing, expired, revoked, and insufficiently privileged tokens must fail appropriately.
- Cover valid maximum-size missions and malformed or oversized inputs and responses, including non-finite coordinates, invalid ranges, unexpected field types, and excessive point counts. Assert rejected imports leave the existing plan intact.
- Retain regression coverage for first save, linked updates, Save as new, and revision conflicts. Test only distinct security and preservation behaviors rather than duplicating one test for every helper.
- Use a small supported-QGIS integration check for protected storage, successful legacy migration, storage-unlock failure, origin changes, and Disconnect. Confirm no raw token remains in ordinary settings after successful migration or forgetting access. Pure-Python network tests do not establish credential-vault behavior.
- Verify rate-limit and quota responses at the website boundary and inspect captured logs for token redaction. Use synthetic credentials and geometry throughout tests.
- The user authorized implementation of this specification through implement-spec, accepting these test boundaries. Test results and remaining limitations are recorded in the implementation report.

## Out of Scope

- Making the repository private or obfuscating plugin code.
- Replacing personal tokens with OAuth or building a custom credential vault.
- Adding new mission capabilities, redesigning the planner, or changing flight calculations.
- Background networking, broad architectural refactoring, or a new testing framework.
- Production penetration testing, deployments, and live credential revocation as part of writing this specification.
- Guarantees against malware already executing as the user on an unlocked machine.

## Further Notes

Implement plugin credential and transport protections first, and verify website ownership and token permissions before treating the connection as release-ready. Backend requirements remain unverified until the website code and tests are inspected. Public source availability is expected and does not itself establish a vulnerability.

The project contribution guide identifies GitHub Issues in dronnix-io/FlyPath as the tracker. GitHub's connector rejected issue creation with HTTP 403 (resource not accessible by integration). The implementation task graph is maintained beside this specification until publication is available. The companion website repository uses Bitbucket and local Markdown tickets.
