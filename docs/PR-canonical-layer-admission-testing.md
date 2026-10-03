# PR: test legacy layer admission with the canonical Python interpreter

## Motivation

The legacy foundation recipe guard uses a stable AST representation from the operational `/usr/bin/python3`, but the generic lint process can use a newer Homebrew Python whose `ast.dump` omits empty fields by default. That newer representation correctly fails closed for archived layer admission, so the prior cross-version test expectation was wrong.

## Implementation

The Make lint target now runs `artifacts.test_foundation` under `/usr/bin/python3` and keeps the remaining artifact suites on the selected `PYTHON`. The Foundation interpreter test discovers installed Python 3.9, 3.12, 3.13, 3.14 and 3.15 executables, admits legacy receipts only when the AST dumper retains empty fields by default, and verifies rejection otherwise. No foundation producer code, locks, dependency recipes or released archives change.

## Validation

The complete Foundation test module passes with `/usr/bin/python3` 3.9.6: 23 tests pass with one expected skip because that interpreter has no function `type_params` AST field. The focused interpreter matrix passes with 3.9.6 and 3.12.14 admitting the archived stock locks and 3.14.7 rejecting them; 3.13 and 3.15 were not installed. Broader source quality remains a separate gate.

## Compatibility and remaining risks

This preserves current operational behavior and exact released lower-layer identities. Python versions with a changed AST dump contract remain unsupported for legacy receipt admission until a separately authenticated verifier-only transition is designed. No layer is rebuilt, republished or relocked.

Linked issue: [canonical layer admission](ISSUE-canonical-layer-admission-testing.md). Integration review: [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).
