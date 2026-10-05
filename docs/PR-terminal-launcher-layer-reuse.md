# Fix: preserve lower archive reuse across the terminal build addition

## Motivation and implementation

The new upper terminal launcher changes shared Bazel hashes and prevents reuse of unchanged lower releases. The verifier permits only the exact reviewed build snapshot and the eight exact canonical layer locks. It maps three shared recipe fields to their recorded hashes while retaining source, lower-layer and compiler checks. Existing stock historical mappings remain bounded. The current fork producer mapping requires the unchanged production AST; production recipe generation is unchanged.

## Validation

Before the fix, all eight consumer regression cases fail. Afterward, 25 focused tests pass on Python 3.12, including mutation rejection across both profiles and all four lower groups. The full source-quality checks pass. Raw current input snapshots and graph evidence remain mandatory in the forthcoming compiled-consumer gates.

## Compatibility and risks

This permits reuse of already released bytes and does not rebuild, retag or rewrite their receipts. Future changes outside the reviewed input hashes fail closed. Related issue: [terminal launcher layer reuse](ISSUE-terminal-launcher-layer-reuse.md).
