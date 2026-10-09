# Add an opt-in D05 native warm-cache diagnostic

## Motivation and behavior

The current stock-native D05 diagnostic needs a way to test whether the measured functional run can reuse the existing BuildKit cache for Feature installation. This adds a standalone helper that runs a bounded, untimed official CLI build against the exact copied D05 workspace, then verifies cache evidence in a later functional-up output. It accepts only an authenticated stock package identity and the two native lanes. The timed runner is not modified by this change; integration remains an explicit worker opt-in.

The helper retains warmup and functional output in fresh private evidence directories and records only digests, durations, lane/fixture and admitted package identity in its receipts. It requires unambiguous cached completions for both common-utils_0 and git_1. Failed warmups, changed inputs, package-install activity, missing stages and unknown progress formats produce not-comparable receipts while preserving raw output.

This is a separately labeled cache-state diagnostic, not a performance qualification. It leaves functional pass/fail and measured durations untouched, uses no regenerated historical reference, does not repair the previously observed cold-run failure, and does not claim the five-cold/ten-warm optimization protocol.

## Validation

Focused CPU-only unit tests cover the exact frozen-lockfile workspace invocation and deadline, private raw-output retention and hashing, timeout preservation, exact D05 inventory, admitted native package identity, both-stage cache proof, unknown JSON event rejection, package-install rejection, candidate/input mismatch and no-overwrite behavior. No build, container runtime, benchmark or repository product source is changed by these checks.

Linked issue: [native D05 warm-cache diagnostic](ISSUE-d05-native-feature-cache-diagnostic.md).
