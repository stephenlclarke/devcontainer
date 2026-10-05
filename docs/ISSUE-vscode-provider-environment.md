# VS Code loses the admitted stock Compose executables

## Problem

The isolated GUI environment retains the Engine socket and private state but drops the admitted Container, Docker and standalone Docker Compose executable paths. The signed Compose facade can consequently discover ambient tools instead of using the selected stock lane.

## Expected behavior

Preserve only the explicit selected provider and executable paths through the isolated environment. Continue excluding credentials and using the private GUI home.

## Validation

A deterministic environment regression asserts each exact path and provider survives while credentials remain excluded. Real VS Code runtime qualification remains required.
