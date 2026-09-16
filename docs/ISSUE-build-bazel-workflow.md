# Issue: qualify native Bazel workflow

## Problem description

The existing family workflow repeatedly rebuilds/retests work and couples recovery to large mutable build trees. The approved redesign assigns dependency scheduling and caching to Bazel, keeps disposable state on the external SSD, and reserves internal storage for retained source and accepted assets.

## Scope and acceptance

First prove native Swift/SQLite targets, version generation, real XCTest/Swift Testing discovery, meaningful coverage, sanitizer configurations and unchanged-input cache reuse. Preserve existing release transactions and unrelated adapter work. This initial slice must not be presented as a full migration.

## Resolution and remaining risk

See [qualification implementation](bazel-workflow.md) and [matching change record](PR-build-bazel-workflow.md). The full external dependency graph, parity downloads, durable release journal, retention/cleanup and CI cutover remain explicit gates.
