# PR: retain bounded foreground failure diagnostics

## Summary

Record a private failure-only journal entry with identity booleans and sanitized process state before propagating an auto-removal failure. The existing owned-identity predicate, allowed exit states and five-second deadline remain unchanged.

## Validation and compatibility

Regressions check state failures and identity drift without raw inspect payloads or identifiers. Real failed fixture results remain failures. Addresses [the issue](ISSUE-foreground-removal-diagnostics.md).

Focused VS Code and foreground suites pass (40 tests) with canonical private SSD scratch. Formatting and source analysis pass; full runtime qualification remains required.
