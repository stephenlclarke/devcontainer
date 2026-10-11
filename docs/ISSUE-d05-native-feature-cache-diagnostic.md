# Issue: verify a native D05 warm-cache diagnostic

## Problem

The released Docker D05 references show both Feature installer stages cached, while the stock-native functional runs can spend substantial time in those stages. A separately labeled native diagnostic needs to condition the current candidate on the same copied D05 workspace before the existing timed functional run, then prove from that run's BuildKit progress that the two named installer stages were cache hits.

## Required behavior

- Admit only the authenticated current stock candidate or finalized stock package and the exact D05 fixture in Apple stock or container-compose.
- Run one untimed official CLI build with the frozen-lockfile option against the same fixture workspace, bounded by the existing 1,800-second command deadline.
- Prove that warmup finished before the existing functional timing interval begins.
- Retain raw build and functional-up output privately, with hashes, durations, fixture inventory and candidate identity digests.
- Mark the diagnostic comparable only when the functional output unambiguously proves cached common-utils_0 and git_1 installer stages and contains no package-install activity.
- Fail closed as not-comparable for a failed or timed-out warmup, unknown progress event, missing or ambiguous stage, package installation, input mismatch or candidate mismatch.

## Scope and remaining risk

This is cache-state evidence for a functional run. It does not change that run's timing or pass/fail result, replay or regenerate historical Docker references, waive the cold-run failure, qualify an optimization, or meet the separate five-cold/ten-warm optimization protocol. The native-runtime worker must opt into it explicitly; ordinary full qualification remains unchanged.
