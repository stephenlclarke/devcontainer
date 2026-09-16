# Native Bazel workflow: qualification checkpoint

## Status and scope

This is an opt-in migration checkpoint, not the replacement release system. Bazel owns native Swift compilation, generated version inputs, C/SQLite interoperability, test discovery and action caching. Existing full-product SwiftPM/CI/release entry points remain unchanged until the remaining graph and release operations are qualified. There is no new Python coordinator: Python here is only unit-test and evidence-validation tooling.

The `//:bazel_qualification` suite contains the existing Model tests (8), SQLite State tests (7), one Core project test, two XCTest/Swift Testing discovery probes, and a shell regression harness for the real version generator. It is deliberately not named `//:unit` and does not represent the full product suite.

## Run locally

Requirements: Apple silicon, full Xcode (validated with Xcode 27 / Swift 6.4), an external volume mounted at `/Volumes/SSD`, Python 3.9 or newer for helper tests, and network access for the first verified tool/dependency download.

```console
make bazel-configure
make bazel-test-tools
make bazel-qualify
Tools/bazel/run.sh test --config=asan //:DevContainerModelTests //:DevContainerStateTests
Tools/bazel/run.sh test --config=tsan //:DevContainerModelTests //:DevContainerStateTests
```

`configure` enrolls the external volume UUID without building. A missing/replaced disk, symlinked managed directory, unsupported storage override or corrupt cached bootstrap executable fails closed. Set `DEVELOPER_DIR` to select another full Xcode installation; standalone Command Line Tools are rejected. Test SDK selection is left to Bazel's Apple toolchain, not overridden with an inherited SDKROOT.

Bazel 8.8.0 and dependency versions are pinned. The launcher downloads Bazel directly and verifies its SHA-256 on every invocation, including cache hits; it does not delegate through Bazelisk or a workspace wrapper. Swift 6 language mode and warnings-as-errors remain enabled. Makefile remains the product version authority. Build identity takes declared `DEVCONTAINER_COMMIT` and `DEVCONTAINER_BUILD_LANE` Bazel defines; qualification defaults explicitly to `unspecified`/`development`, not a fabricated release identity.

## Storage and evidence

| Data | Location / policy |
| --- | --- |
| Source and tracked design | Internal repository/worktree |
| SSD enrollment | `~/Library/Application Support/ContainerFamily/retained/workflow/ssd-volume.uuid` |
| Tool downloads, repository cache, action cache, Bazel outputs | `/Volumes/SSD/cf/bazel/` |
| Test scratch | Bazel-assigned `TEST_TMPDIR` under the enrolled SSD root |
| Per-invocation build events | Printed `invocations/run.*/events.json` on SSD |
| Accepted long-term evidence | Copy validated reports and their input identity to internal retained storage before deleting scratch; automated promotion/retention is not implemented yet |

On Darwin, Foundation can choose the system temporary directory before consulting `TMPDIR`. Migrated SQLite fixtures therefore explicitly use `TEST_TMPDIR`. This checkpoint does **not** claim that all unmigrated product/test temporary writes have been audited. The launcher rejects broad `clean` and release commands; it does not prune unrelated files, runtime instances or GitHub assets. Size-bounded cache eviction and owned-run cleanup remain migration work.

The launcher automatically validates qualification coverage before returning success and writes `qualification.json` beside its event file. To independently check a completed invocation before another build overwrites its output tree:

```console
python3 Tools/bazel/check_evidence.py /Volumes/SSD/cf/bazel/invocations/run.EXAMPLE/events.json /Volumes/SSD/cf/bazel/output/WORKSPACE_HASH/execroot/_main/bazel-out/_coverage/_coverage_report.dat
```

Replace the example paths with the emitted locations. `--warm` additionally requires every qualification test to have been cached. The validator rejects missing targets, failed/incomplete builds, retries, missing/skipped test cases and empty coverage. It reads evidence only and never launches or retries work. Native Bazel action statistics in the event file provide the separate compile/cache evidence.

## Qualification evidence

Validated locally on 2026-09-16 with Xcode 27.0 build 27A266a:

- All five qualification targets pass: 18 Swift test cases plus the version-generator shell harness.
- Combined subset coverage contains 1,140 hit lines / 2,063 measured lines. This is **not** overall repository coverage, a 90% claim, or release acceptance.
- An unchanged repeated coverage invocation reused all five test results and executed no tests or Swift compilations. Bazel reported 0.072 seconds for that warm invocation; this is a build-cache diagnostic, not a quiet-machine runtime benchmark.
- Model and State tests pass independently with Address Sanitizer and Thread Sanitizer (15 cases per configuration). This does not establish whole-product leak freedom; the existing dedicated leak harness is not migrated.

## Pinned compatibility adaptations

The small maintained `rules_swift` patch makes test-source writes report errors, avoids Foundation atomic replacement outside the SSD sandbox for disposable generated/XML outputs, and registers Bazel's LCOV merger. The project version generator instead retains atomic publication using a temporary sibling and rename; regression tests cover invalid inputs, unchanged contents and failed output publication.

The test wrapper exports LLVM profiles from the same test execution, without a second test run. Its intermediate `.profdata` stays outside the LCOV merger's input directory because Bazel rejects mixed profile/LCOV inputs. Bazel then filters and merges LCOV against its declared source manifest. Framework-only probes legitimately have no instrumented production sources. No tests are excluded to inflate the reported percentage.

References: [rules_swift](https://github.com/bazelbuild/rules_swift), [Bazel caching](https://bazel.build/remote/caching), [Build Event Protocol](https://bazel.build/remote/bep), [Bazel coverage collector](https://github.com/bazelbuild/bazel/blob/8.8.0/tools/test/collect_coverage.sh).

## Remaining migration gates

1. Import the locked external Swift package graph and migrate complete product/test targets for both stock and enhanced dependencies, without wrapping SwiftPM as an opaque build action.
2. Add native coverage acceptance against the actual maintained scope, full sanitizers/leak harnesses, and current Sonar reporting.
3. Download and verify published GitHub binaries for runtime parity. Reference products must not be rebuilt. Keep real runtime tests and fresh quiet-host benchmarks outside stale test-cache reuse.
4. Implement durable release checkpoints, exact artifact identity, preflight authorization, signing/notary resume, immutable publication and Homebrew verification as thin non-cacheable host operations.
5. Implement internal evidence retention and bounded owned-run SSD/local/GitHub cleanup. Prove restart/recovery and repeated-request deduplication before CI cutover.

Until these gates pass, do not use this subset to promote a release, assert full parity, or replace protected CI checks.
