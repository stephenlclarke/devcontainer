# Compose discovery fails under the official CLI

## Problem

The installation-owned Node runtime launches the signed Compose facade with a Unix socket as standard input. Darwin reports `EOPNOTSUPP` when the process runner queries its terminal foreground group. The runner accepts only `ENOTTY`, so Compose discovery fails before its admitted child is launched. Direct terminal, pipe and null-input probes do not reproduce this failure.

## Expected behavior

Treat socket input as a nonterminal and preserve its bytes, the child's output and exact exit status. Continue rejecting invalid descriptors and unexpected terminal-query errors. Preserve foreground ownership checks and signal forwarding for real terminals.

## Validation

A real Unix socketpair subprocess fixture reproduces Darwin's error, passes input through the inherited runner, and checks exact output and exit status. Deterministic error cases retain invalid-descriptor and I/O rejection. Fresh release qualification remains required.
