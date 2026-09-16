# Issue: implement the native Bazel workflow

## Problem description

The existing family workflow repeatedly rebuilds/retests work and couples recovery to large mutable build trees. The approved redesign assigns dependency scheduling and caching to Bazel, keeps disposable state on the external SSD, and reserves internal storage for retained source and accepted assets.

## Scope and acceptance

Implement the complete native stock/enhanced product and test graph, version generation, real XCTest/Swift Testing discovery, meaningful coverage and unchanged-input cache reuse. Retain evidence before output reuse, reject mixed-source receipts and keep scratch on the enrolled SSD. Continue to artifact-only parity, recovery/publication, family adoption and ownership-based hygiene before replacing production workflows. Preserve existing release transactions and unrelated adapter work; native compilation alone is not the full migration.

## Resolution and remaining risk

See [implementation status](bazel-workflow.md) and [PR 83](PR-83.md). The complete native graph, transactional evidence/candidate retention, archive restore, pinned GitHub binary acquisition and owned invocation cleanup are implemented. Host and quality qualification, complete artifact-only parity, durable release effects, family adoption, broader cleanup and CI cutover remain explicit gates.
