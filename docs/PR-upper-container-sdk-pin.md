# Pull request: preserve unchanged layers when updating Container SDK

## Motivation and change

The enhanced Container SDK needs a new release containing the local image-config-ID lookup correction. The root manifest update should preserve unchanged enhanced Foundation, Containerization and Engine API artifacts and all four stock artifacts.

The reader admits only the original eight compact locks and exact historical recipes. It verifies the actual resolved origin hash and normalizes only the enhanced Container revision and origin hash back to immutable baseline bytes. Every other producer node and verifier header remains bound, along with source pins, lower releases, compilation settings, toolchain and downloaded archive validation. The stock SDK compatibility branch requires the original source-graph receipt digest as well as its recomputed digest and exact loaded stock inputs. The enhanced SDK is rejected after the pin changes and must be freshly produced and published.

## Validation

The focused foundation suite passes all 16 tests, including real consumer admission for all eight baseline groups, the three unchanged enhanced groups and all four stock groups after a synthetic upper-pin update. Negative cases cover stale enhanced SDK, unrelated manifest/lock/recipe/producer/header/lower changes, duplicated pins, an untrue origin hash, added fields with a recomputed stock receipt hash, and modified compact-lock bytes or archive identity. Toolchain observations are mocked; consumer recipe and source-graph validation run unchanged. Immutable snapshots preserve the original nine compact locks when the live enhanced SDK lock advances; a regression exercises that future state.

A standard-library statement-line trace measured the four new or substantially changed compatibility functions at 90/97 lines (92.8%). The complete producer module measured 364/717 lines (50.8%) in this focused suite; production, mirror and broader toolchain paths remain outside this regression boundary and require the complete product qualification. These counts use executable AST statement starts rather than coverage.py instrumentation.

Full source review cleared the compatibility policy. New compiled SDK publication, downloaded consumption and the eventual immutable product qualification remain required. Historical product packages and runtime evidence remain unchanged. See [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83) and [the layer workflow](devcontainer-layers.md).
