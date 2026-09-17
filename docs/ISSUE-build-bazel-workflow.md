# Issue: implement the native Bazel workflow

## Problem description

The existing family workflow repeatedly rebuilds/retests work and couples recovery to large mutable build trees. The approved redesign assigns dependency scheduling and caching to Bazel, keeps disposable state on the external SSD, and reserves internal storage for retained source and accepted assets.

## Scope and acceptance

Implement the complete native stock/enhanced product and test graph, version generation, real XCTest/Swift Testing discovery, meaningful coverage and unchanged-input cache reuse. Retain evidence before output reuse, reject mixed-source receipts and keep scratch on the enrolled SSD. Continue to artifact-only parity, recovery/publication, family adoption and ownership-based hygiene before replacing production workflows. Preserve existing release transactions and unrelated adapter work; native compilation alone is not the full migration.

Final delivery includes stable GitHub/Homebrew releases for both projects, full unit/integration/parity validation of the published downloads, public quiet-host benchmark reports/raw evidence, and a final documentation phase producing both DocC sites and installation-first live VHS demos. The Compose demo must exercise the complete default monitoring stack, visibly showing empty, running, stopped and restarted states. See the [delivery contract](bazel-workflow.md#final-delivery-and-public-evidence) for provenance, storage and publication requirements.

## Resolution and remaining risk

See [implementation status](bazel-workflow.md) and [PR 83](PR-83.md). The complete native graph, transactional evidence/candidate retention, archive restore, pinned GitHub binary acquisition and owned invocation cleanup are implemented. Host and quality qualification, complete artifact-only parity, durable release effects, family adoption, broader cleanup and CI cutover remain explicit gates.

The Docker reference now has retained published tool/image/client inputs and an opt-in Bazel VM adapter with the shared Engine assertions. E01/E02/E03/E05/E06 have real Docker functional passes and verified cleanup. New published campaigns share a complete cross-lane fingerprint. Full three-lane qualification, interrupted-VM recovery and the remaining fixtures are still required; these focused results are not stable-release completion.
