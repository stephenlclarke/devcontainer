# Issue: run bounded native-only package diagnostics

## Problem

The finalized-package controller's supported component mode runs one fixture across Docker, Apple stock and container-compose. That is too broad when the immediate question is whether selected behavior works in the two native provider lanes while reusing an authenticated Docker result. Direct lane invocations would skip the shared host guard, runtime lease, package admission and restoration checks.

## Required behavior

- Allow a finite selection of C03 resources, D05 features and E07 attachment fixtures against either the exact signed finalized package or an exact prepared schema-2 stock candidate, plus authenticated native providers.
- Reuse the existing native startup preflight, runtime lease, host guard, service restoration and fixture cleanup checks.
- Do not invoke the Docker lane or VS Code suite, and do not compare or graft external Docker results into the current run's lane evidence.
- Emit a diagnostic receipt with `releaseQualified: false`; candidate receipts must say `signatureVerified: false`. Optionally retain an original Docker reference only after checking its independently supplied SHA-256.
- Preserve the existing component and complete 84-observation qualification paths.

## Scope and remaining risk

This is a bounded diagnostic path, not a functional parity or performance qualification. A candidate must be re-admitted from the exact retained evidence, match the clean product source commit, and pass complete archive and private-runtime inventory checks in both provider lanes. Candidate evidence never substitutes for signature, notarization, release qualification or publication.
