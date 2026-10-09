# PR: Bind native candidate diagnostics to their admitted package

## Summary

Native owned-guest preparation now accepts the candidate-diagnostic guard only when its candidate invocation, receipt SHA-256, archive SHA-256, stock profile, source commit and finite fixture selection match the already-admitted runner identity. The evidence root, campaign and active lane ownership checks remain in force. Finalized full and component guard behavior is unchanged; finalized native diagnostics also bind their selected fixtures.

## Implementation

The controller records candidate identity fields from the shared stock-lane admission in its private runtime guard. The owned guest admission helper reconstructs the expected guard identity from the admitted candidate, rejects unsupported diagnostic fixture sets, and compares the complete identity before accessing the active provider HOME.

## Validation

- `python3 -m unittest test_owned_guest_fixture test_qualify_finalized_package test_run_lane` — 169 tests passed
- `git diff --check` — passed
- `markdownlint-cli2 README.md docs/ISSUE-native-diagnostic-candidate-guard.md docs/PR-native-diagnostic-candidate-guard.md` — passed
- `python3 -m py_compile Tools/parity/owned_guest_fixture.py Tools/parity/qualify_finalized_package.py Tools/parity/test_owned_guest_fixture.py` — passed

## Compatibility and release impact

This is guard admission for non-qualifying native diagnostics. It does not change candidate package admission, signed-package qualification, runtime fixtures or release authority.

## Remaining risks

The corrected guard path still requires a fresh native diagnostic run to demonstrate the candidate case proceeds through owned guest preparation. Existing failed runtime evidence remains preserved and must not be relabeled.
