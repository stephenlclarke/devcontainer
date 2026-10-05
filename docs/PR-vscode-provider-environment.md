# PR: retain selected provider paths in VS Code

## Summary

Add the admitted Compose provider, Container executable, Docker executable and standalone Docker Compose executable to the existing GUI environment allowlist. The existing native provider launch override remains in place.

## Validation and compatibility

The isolated environment regression checks all four values and credential exclusion. This changes only the parity runner's handoff and does not widen the product's executable discovery. Full runtime qualification remains required. Addresses [the issue](ISSUE-vscode-provider-environment.md).

Focused VS Code and foreground suites pass (40 tests) with canonical private SSD scratch. Formatting and source analysis pass; full runtime qualification remains required.
