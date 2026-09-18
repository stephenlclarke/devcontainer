# Issue: implement the native Bazel workflow

## Problem description

The existing family workflow repeatedly rebuilds/retests work and couples recovery to large mutable build trees. The approved redesign assigns dependency scheduling and caching to Bazel, keeps disposable state on the external SSD, and reserves internal storage for retained source and accepted assets.

## Scope and acceptance

Implement the complete native stock/enhanced product and test graph, version generation, real XCTest/Swift Testing discovery, meaningful coverage and unchanged-input cache reuse. Retain evidence before output reuse, reject mixed-source receipts and keep scratch on the enrolled SSD. Continue to artifact-only parity, recovery/publication, family adoption and ownership-based hygiene before replacing production workflows. Preserve existing release transactions and unrelated adapter work; native compilation alone is not the full migration.

Final delivery includes stable GitHub/Homebrew releases for both projects, full unit/integration/parity validation of the published downloads, public quiet-host benchmark reports/raw evidence, and a final documentation phase producing both DocC sites and installation-first live VHS demos. The Compose demo must exercise the complete default monitoring stack, visibly showing empty, running, stopped and restarted states. See the [delivery contract](bazel-workflow.md#final-delivery-and-public-evidence) for provenance, storage and publication requirements.

## Resolution and remaining risk

Docker shutdown could be verified while a later crash during scratch deletion or guard clearance left no supported recovery path. Recovery now recognizes the Docker journal separately, verifies process/socket closure again, and uses a durable directory-identity authorization written before normal or resumed deletion. It preserves failed-case evidence and refuses uncertain startup/shutdown rather than rerunning the VM. Full live interrupted-process reconciliation is still required.

Creating an archive alone does not prove that its installed layout runs or that its bytes survive the test workflow. Native package verification now authenticates the actual extracted products and executes safe CLI/provider-fixture checks on SSD, retaining archive bytes and test evidence under one invocation. Uncertain process cleanup must preserve both the private home and extracted package. This remains unsigned local package proof; Homebrew installation and complete distribution qualification remain required.

Native consumer CLI component tests could pass without emitting LLVM coverage because their sanitized child environment discarded profiling output and the shell target did not declare the executable for export. The shared evidence contract now supports a separate exact unit-plus-CLI inventory, preserving unit-only history and binding the selected inventory at the 90% gate. Compose owns the executable instrumentation and additive target inventory; no source exclusions or lower thresholds are introduced.

See [implementation status](bazel-workflow.md) and [PR 83](PR-83.md). The complete native graph, transactional evidence/candidate retention, archive restore, pinned GitHub binary acquisition and owned invocation cleanup are implemented. Host and quality qualification, complete artifact-only parity, durable release effects, family adoption, broader cleanup and CI cutover remain explicit gates.

The Docker reference now has retained published tool/image/client inputs and an opt-in Bazel VM adapter with the shared Engine assertions. E01/E02/E03/E05/E06 have real Docker functional passes and verified cleanup. New published campaigns share a complete cross-lane fingerprint. Full three-lane qualification, interrupted-VM recovery and the remaining fixtures are still required; these focused results are not stable-release completion.
