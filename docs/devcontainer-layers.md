# Devcontainer dependency layers

The local Bazel workflow separates compiled dependency reuse from source-unit testing. Both stock and enhanced profiles retain their exact package pins. The compiled path builds all four executables: `devcontainer`, `devcontainer-compose`, `devcontainer-engine`, and `devcontainer-docker`.

## Released dependency chain

| Layer | Released input consumed | Recorded assets |
| --- | --- | --- |
| ArgumentParser | Existing shared compiled release | `Tools/bazel/artifacts/argument-parser.lock.json` |
| Foundation packages | ArgumentParser | `foundation-stock.lock.json`, `foundation-enhanced.lock.json` |
| Containerization | Foundation and ArgumentParser | `containerization-stock.lock.json`, `containerization-enhanced.lock.json` |
| Engine API | Foundation and ArgumentParser | `engine-api-stock.lock.json`, `engine-api-enhanced.lock.json` |
| Container SDK | Containerization and Engine API, with their lower inputs | `container-sdk-stock.lock.json`, `container-sdk-enhanced.lock.json` |
| Devcontainer executables | Complete released dependency chain | Built locally from the current source checkout |

All dependency locks live in `Tools/bazel/artifacts`. Each release has the compiled archive and its qualification evidence. The importer checks source pins, compiler and SDK identity, import recipe, release identity, archive contents, and the evidence sidecar. A missing or mismatched input fails; it does not silently compile a replacement from source. These Swift binaries require the recorded Apple silicon toolchain. A toolchain change requires newly qualified dependency assets.

## Run the checks

After the normal [Bazel setup](bazel-workflow.md#commands), use two fresh absolute output directories:

```sh
make bazel-compiled-consumers LAYER_EVIDENCE=/absolute/path/new-consumer-evidence
make bazel-layers LAYER_EVIDENCE=/absolute/path/new-source-evidence
```

Both commands run stock and enhanced by default. Set `LAYER_PROFILE=stock` or `LAYER_PROFILE=enhanced` to select one. They stop on the first failure, retain command and test evidence, and clean up owned child processes on cancellation. Admitted evidence requires a clean, unchanged checkout; `LAYER_DEVELOPMENT=1` is only for diagnostic work.

The compiled-consumer check builds all four executables and examines their actual link inputs. It verifies the exact released archives, Swift modules and headers, and rejects compilation or archiving of imported dependency targets. This checks actual binary reuse, not only the presence of a dependency lock.

The source check retains all eleven original unit suites in six ordered groups:

| Order | Group | Suites |
| --- | --- | --- |
| 1 | Model | Model |
| 2 | Runtime foundation | Process, State |
| 3 | Core | Core |
| 4 | Adapters | Compose Provider, Docker API, Docker Client |
| 5 | Host | Apple Runtime, Service |
| 6 | CLI | CLI, Compose CLI |

Individual `bazel-layer-model`, `bazel-layer-runtime-foundation`, `bazel-layer-core`, `bazel-layer-adapters`, `bazel-layer-host`, and `bazel-layer-cli` Makefile targets expose these source suites. Source tests keep their own build configuration and cache. Each admitted result uses the original retained test XML for its exact invocation, even after another profile has reused Bazel's output paths.

## Package the current products

`make bazel-package BAZEL_PROFILE=stock` or `BAZEL_PROFILE=enhanced` creates and checks an unsigned candidate archive using the released dependency chain. Packaged executables use the production feature configuration, separately from Swift source-unit tests. This archive is an input to further release qualification; its receipt keeps `distributionReady: false`.

## Updating a dependency

Publish foundation in both profiles first and verify the downloaded assets before committing its two locks. Build Containerization and Engine API as siblings against those committed foundation locks, publish and verify all four assets, then commit their locks together. Finally publish and verify Container SDK in both profiles against the committed sibling layers. The maintained `foundation.py` producer and `layer_release.py` admission/publication commands reject dirty source, changed inputs, incomplete test results, and overwriting an existing release. Keep the current exact locks when their inputs are unchanged.

## Qualification boundary

Dependency release and binary-consumption checks do not qualify a stable devcontainer product release. Full coverage and code quality, sanitizers, package signing and notarization, installation, runtime restoration and the original cross-backend parity contract remain separate gates. Hosted CI keeps its source quality and test lane. Performance optimisation and new benchmark campaigns are outside this layering change.
