# PR: restore admitted native backend selection after child filtering

## Implementation

Native Engine setup assigns the runner lane's backend after establishing the private configuration, socket and state paths. VS Code launch independently assigns its admitted native lane after GUI environment filtering. Neither filter inherits ambient backend values, and the Compose facade continues to derive runtime metadata from the resolved backend.

## Validation

The two mocked entrypoint regressions fail against the original production source for both stock and Q lanes, then pass with this fix: an ambient operator choice is excluded, Engine provider and actual CLI/Compose child environments agree, and actual GUI launch receives the selected backend after filtering. The complete `test_run_lane` and `test_run_vscode` modules pass all 70 tests using Python 3.12. These tests validate launcher selection; fresh live qualification of the complete release remains required.

Linked issue: [native backend selection](ISSUE-runtime-backend-selection.md).
