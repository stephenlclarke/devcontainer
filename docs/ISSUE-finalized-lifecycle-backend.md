# Issue: finalized lifecycle tests override installed backend paths

## Problem description

The finalized native package owns its Docker and Compose adapter paths. The parity harness still supplied development backend flags to `up`, feature-lock checks, reuse and rebuild commands. The installed lifecycle facade correctly rejected these overrides before running the workload, leaving eleven lifecycle cells failed in the D26 campaign.

## Required behavior

Finalized native lanes must use the installed adapters without adding caller backend overrides. Docker and development lanes must retain their explicit backend paths. Workload arguments, including literal flag names after `--`, must remain unchanged.

## Validation and remaining work

Focused regressions fail with the four previous argument builders and pass with the corrected builders: 40 harness tests pass. A fresh full release campaign is required; the previous failed campaign remains failed.
