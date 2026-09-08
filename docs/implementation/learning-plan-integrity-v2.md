# Learning plan integrity v2

The shipped planner now distinguishes three records that were previously conflated.

- `determinism_inputs.fingerprint_schema_version: 2` identifies the exact supplied profile,
  generated module payload, corpus snapshot, corpus hashes, and personalization rules. The
  external identifier remains 12 hexadecimal characters; it is an identity aid, not proof of
  semantic equivalence. Existing database rows are preserved and are not rehashed.
- `source_selection` contains inspectable lexical candidates with stable document/chunk
  locators. Candidates always remain `source_support_unverified`; lexical overlap is never
  presented as support for a claim. Human review or a later explicit support-judgment path is
  required to promote a passage.
- `output_policy` is opt-in. With no `selected_wings`, the plan is encounter-only. Selecting an
  artifact descriptor does not authorize publication.

The deterministic local adaptation record uses only learning purpose, prior task evidence,
prerequisites, medium, and access conditions. Other profile fields remain part of input identity
but do not gratuitously change the instructional route. Private profile data remains local and
must not be committed as fixtures.

## Migration and rollback

Version 1 plan IDs and rows remain valid historical records. New generations use version 2 and
append new rows and ledger events. Consumers must read the fingerprint version before comparing
IDs across generations. Rollback means deploying the previous planner while retaining version 2
rows; it must not delete or rewrite either generation. The retained 48-bit shortened digest has
a birthday-bound collision risk and must not be used as a security token or sole database key.
