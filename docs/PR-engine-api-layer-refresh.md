# PR: retain exact lower layers across the Engine API source refresh

## Motivation and implementation

The Engine API transport correction changes the selected stock and enhanced Engine sources and the enhanced Container nested dependency. The finite compatibility verifier binds that reviewed transition to exact source-file hashes and permits only the four unchanged Foundation and Containerization canonical locks. All historical transition checks remain available for their original inputs. ArgumentParser retains its independent source and toolchain admission.

The reviewed Engine API stock source is `36de2d66d4a1f7eb48c08d94cf1444f93d5f9c77`, enhanced source is `6e8c932fc8755a4b922fd239426e9029be0554e0`, and enhanced Container source is `906014c854a09df4283316289bc755a925f81fe3`. The Devcontainer manifest SHA256 is `983e37299d3315b8ab836e56444f049ea6acfb67863d9dafbea3f7c1570b33d3`; enhanced and stock resolved-lock SHA256 values are `8a75925150ca36efd92d11767d4d2e4ee68f420361667228ba9abc9ed07c3325` and `c5dc4990e57aae68e649864f7914605a4d374563950b460191eacec46c2c084d`. Both root lock origin hashes bind that exact current manifest. Historical fixtures reconstruct the preceding reviewed inputs, including their distinct enhanced and stock origin hashes, and verify their original file digests before replaying archived admission.

The reused archives keep their exact package pins, lower archives, qualification sidecars, compiler and SDK identities, importer, production AST and shared build snapshot. New Engine API and Container SDK archives must pass ordinary exact recipe checks; their previous locks are rejected under the new transition. No producer, importer, shared BUILD/module recipe or lower archive rebuild is part of this change.

## Validation

Root validated passing 221 stock and 189 enhanced test functions, strict warnings-as-errors validation in both profiles, and a deterministic startup regression that fails without the correction and passes with it. These are source-component results, not compiled-layer release evidence. All 27 focused Foundation compatibility tests and 26 native-layer/source-graph/compiled-consumer tests pass with Python 3.12. The historical fixtures and exact production verifier fingerprint remain preserved; formatting and touched Markdown checks pass. New layer asset publication remains pending. Its tests cover all four unchanged lower locks and reject unreviewed source-file, pin, origin-hash, location, canonical-lock, lower-asset and shared recipe changes while preserving the immutable historical fixtures. The new Engine API pair must be qualified, published, downloaded and admitted before its locks are committed. SDK qualification follows from that clean committed lower graph.

## Compatibility and remaining gates

Dependency publication establishes exact compiled input provenance, not stable Devcontainer release authority. Final source quality, coverage, sanitizers, signing, notarization, installation, restoration and cross-backend parity remain separate required gates. Preserve the original failed upload evidence and historical published asset receipts.

Linked issue: [Engine API layer refresh](ISSUE-engine-api-layer-refresh.md).
