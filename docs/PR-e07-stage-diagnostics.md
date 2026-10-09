# PR: retain payload-free stage diagnostics for failed E07 attachments

## Summary

Failed E07 fixture results now include a bounded `diagnosticTrace` projection of attachment events. It records the completed stage label, transfer elapsed nanoseconds, optional status/error class, and allowlisted byte/EOF counters. Routes, IDs, response bodies, payloads, raw diagnostics and unknown fields are omitted. The original 30-second transfer deadline and all functional checks remain unchanged.

## Validation and compatibility

The focused offline regression injects a timeout with route, response-body and private stream fields, then proves only the safe projection appears in the failed fixture result. It passes, and `git diff --check` passes. No runtime, package build or timing run was performed. The two original stock E07 failures remain failures; the next diagnostic run must use a newly admitted finalized package whose source and harness identities match this change.

Addresses [the issue](ISSUE-e07-stage-diagnostics.md).
