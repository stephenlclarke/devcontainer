# Issue 38: select the service executable from the active SwiftPM build

## Problem

`ServiceCommandIntegrationTests.engineExecutable()` preferred a coverage-built `devcontainer-engine` whenever it fell back to enumerating `.build`, even during a normal debug test run. A retained coverage binary could therefore be executed instead of the product built for the active test invocation.

## Reproduction

1. Retain an older `.build/coverage/.../devcontainer-engine`.
2. Add or change an option in the current debug product.
3. Run `swift test --filter ServiceCommandIntegrationTests`.
4. Observe the integration test launch the stale coverage executable and reject the new option.

## Expected behavior

The helper must select a coverage executable only when the active test bundle belongs to the coverage build. A normal test run must select the normal debug executable.

## Impact

The defect can produce a false integration failure and can also test code other than the source candidate under review. It does not affect an installed runtime executable.

## Acceptance

- Normal debug tests select the normal debug service product even when a stale coverage product exists.
- Coverage tests continue to select the instrumented service product.
- Both standalone-listener and private-provider-session subprocess tests pass.

## Tracking

- GitHub issue: [#38](https://github.com/stephenlclarke/devcontainer/issues/38)
