# PR: support Node's socket-backed subprocess input

## Summary

Recognize Darwin's `EOPNOTSUPP` terminal-query result as nonterminal input alongside `ENOTTY`. This permits the official Dev Containers CLI to launch its signed Compose facade without changing executable selection, output, status, terminal ownership or signal policy.

## Validation

The process probe installs an actual Unix socketpair as stdin in an isolated subprocess, verifies the kernel error and exercises input, output and child status. Existing foreground ownership and error regressions remain. The focused stock process suite passes in retained invocation `5556a625-8616-4d32-bfec-4f9e0adcadc2` (26 cases), including the real socket fixture. Formatting and source analysis pass. Full native and runtime release gates are pending.

## Compatibility and risk

Other terminal-query errors still fail. No provider fallback, pinned upstream CLI changes or parity waivers are introduced. Addresses [the issue](ISSUE-node-socket-stdin.md).
