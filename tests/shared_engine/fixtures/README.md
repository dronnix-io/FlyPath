# Shared engine fixtures

Fixtures are versioned JSON cases consumed by the Python core, website adapter,
and plugin adapter tests.

Each case contains:

- `input.json`: a complete environment-independent request
- `expected.json`: reviewed results or invariants
- `provenance.md`: source, reviewer, reference, and caveats

Never regenerate expected files automatically. A changed expected route is a
reviewable planning-behavior change.

`schema-example.json` establishes naming, units, coordinate representation,
and version fields. It remains illustrative until calculation rules and the
first drone-profile version are approved.

`reported-mission-126-input.json` preserves the inputs from the reported
website/plugin mismatch. It intentionally has no expected output yet because
neither existing implementation is accepted as the calculation reference.
