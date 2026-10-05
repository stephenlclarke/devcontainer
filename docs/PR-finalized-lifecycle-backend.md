# Fix: use installed lifecycle backends during finalized parity

## Motivation

Finalized native lifecycle calls failed because the harness overrode installation-owned backend paths.

## Implementation

A single lane-aware argument helper omits harness-generated Docker backend flags only for finalized native lanes. The four lifecycle call sites use this helper. Existing Docker/development behavior and literal workload arguments remain intact, and the production override guard stays enforced.

## Validation

The 40 focused harness tests pass, including mode selection, the actual call sites and literal arguments. Restoring the old argument pairs fails the new regression. Full release qualification remains pending.

## Compatibility and risks

This changes test invocation only. Installed packages continue to reject user backend overrides. Related issue: [finalized lifecycle backend ownership](ISSUE-finalized-lifecycle-backend.md).
