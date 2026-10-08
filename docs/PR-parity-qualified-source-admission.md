# Admit parity evidence for its exact product source

## Result

The parity workflow authenticates the retained qualification receipt before reading its product source. It compares the fixed native input closure against the workflow tooling commit, then checks out the qualified product source into a separate clean directory and invokes the unchanged strict verifier there. The run SHA and receipt source remain distinct identities.

## Admission boundary

The closure covers production sources, tests and fixtures, plugins, package pins, Bazel/build definitions, and the maintained `Tools/bazel`, `Tools/parity`, `Tools/testing`, `Tools/version-generator` and `Tools/ci` inputs. It excludes release publication tooling, workflow YAML, documentation, and only the named CI workflow-regression test because those do not affect the product or runtime harness exercised by the qualification. All other `Tools/ci` helpers remain included. A changed native input fails closed and requires fresh runtime qualification.

The retained GA 1.1.0 campaign remains qualified only for tagged product source `297c9dde27f6519d6ba5b02314622e4a21c88702`. Current-source layer, compiled-consumer, package and coverage gates are separate evidence and do not relabel the GA runtime proof. No runtime campaign, notarization, package publication or release metadata is repeated by this workflow repair.

## Compatibility and validation

The original verifier and its exact five-file artifact inventory remain unchanged. A temporary-Git regression executes the workflow's real admission script and covers source drift, accepted tooling/documentation changes, and tampered qualification bytes. Focused Homebrew and workflow tests, the original verifier against retained local authority, Actionlint, Markdownlint and `git diff --check` validate the change.
