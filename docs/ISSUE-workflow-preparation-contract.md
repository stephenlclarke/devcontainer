# Issue: align workflow regression with exact dependency preparation

## Motivation and required behaviour

PR 83 CI fails its stock-Apple workflow regression before compilation because its textual Makefile boundary assumes `swift-test:` and `coverage:` have no prerequisites. The maintained targets now require exact SwiftPM patch preparation. The old selector raises a substring error and never checks the stock workflow contract.

Assert the two exact preparation prerequisites, then inspect the existing test recipe between those current target boundaries. Preserve stock lock, strict compilation, single-attempt testing and runner assertions. No build implementation or dependency recipe changes are required.

## Validation and compatibility

Run the workflow-artifact regression module, including the previously failing stock-Apple case, and lint the handoff documents. Exact-head GitHub CI remains the integration authority. See [implementation](PR-workflow-preparation-contract.md) and [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).
