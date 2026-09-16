# Native Bazel workflow

## Status and scope

This is an opt-in native product build, not yet the replacement release system. Bazel owns native Swift/C compilation, the complete pinned package graph, generated version inputs, C/SQLite interoperability, test discovery and action caching. All three executables (`devcontainer`, `devcontainer-compose`, `devcontainer-engine`) build against both dependency profiles. Existing SwiftPM/CI/release entry points remain unchanged until the host and release operations are qualified. There is no new Python build coordinator: the small Python helpers validate and retain evidence; they never schedule or retry builds.

`//:unit` runs all ten deterministic source test targets: 232 cases with enhanced dependencies and 221 with stock dependencies. `//:bazel_qualification` remains a smaller compiler/framework/generator diagnostic and must not substitute for the unit suite. Native executable modules are compiled once and shared with their importing tests. Third-party packages retain their declared Swift language settings; Swift 6 and warnings-as-errors apply to project code.

## Run locally

Requirements: Apple silicon, full Xcode (validated with Xcode 27 / Swift 6.4), an external volume mounted at `/Volumes/SSD`, Python 3.9 or newer for helper tests, and network access for the first verified tool/dependency download.

```console
make bazel-configure
make bazel-test-tools
make bazel-build
make bazel-unit
make bazel-build BAZEL_PROFILE=stock
make bazel-unit BAZEL_PROFILE=stock
make bazel-package
make bazel-acquire-releases
make bazel-cleanup
make bazel-qualify
Tools/bazel/run.sh test --config=asan //:DevContainerModelTests //:DevContainerStateTests
Tools/bazel/run.sh test --config=tsan //:DevContainerModelTests //:DevContainerStateTests
```

`configure` enrolls the external volume UUID without building. A missing/replaced disk, symlinked managed directory, unsupported storage override or corrupt cached bootstrap executable fails closed. Set `DEVELOPER_DIR` to select another full Xcode installation; standalone Command Line Tools are rejected. Test SDK selection is left to Bazel's Apple toolchain, not overridden with an inherited SDKROOT.

Bazel 8.8.0 and dependency versions are pinned. The launcher downloads Bazel directly and verifies its SHA-256 on every invocation, including cache hits; it does not delegate through Bazelisk or a workspace wrapper. `Package.resolved` and `Package.stock.resolved` supply exact source revisions through the native Swift package rules. Select one profile with `--config=stock` or `--config=enhanced`; independent dependency overrides and mismatched profile defines are rejected. Makefile remains the product version authority. Build identity takes declared `DEVCONTAINER_COMMIT` and `DEVCONTAINER_BUILD_LANE` Bazel defines; development defaults explicitly to `unspecified`/`development`, not a fabricated release identity.

The Keychain-backed service process tests and live Apple-service logging test are explicit, uncached host-integration targets, not silently skipped unit cases. They require `DEVCONTAINER_HOST_INTEGRATION=1`; the launcher preserves only a validated `0` or `1` for that opt-in. The logging target is incompatible with the stock profile because that upstream API is absent. These targets remain unqualified for unattended execution and are not a parity claim.

## Native candidate archives

`make bazel-package` requests optimized native products and creates `//:candidate_archive` without invoking SwiftPM or another build. The archive contains all three arm64 executables, plugin layout, launchd template, project legal files, gathered compiled-dependency license texts, the selected dependency lock and a digest-bearing candidate identity. It reuses the repository's deterministic archive writer. This is an **unsigned candidate**, explicitly marked `distributionReady: false`, not a signed release or a replacement for the complete release SBOM/notices/signing gate. Default source identity remains `unspecified` until a frozen-source build supplies it.

The launcher retains the archive and receipt bytes internally with the build invocation. Restore a successful retained candidate to disposable SSD storage without Bazel or compilation:

```console
Tools/bazel/run.sh restore-candidate INVOCATION-ID
```

Use the ID printed after the package build. Restore authenticates both stored blobs and the archive digest in its receipt, refuses failed invocations or conflicting destinations, and reuses an unchanged restored copy. It does not claim that a restored candidate is signed, reviewed or approved for publication.

## Published binary inputs

`make bazel-acquire-releases` reads the reviewed `Tools/bazel/releases.lock.json`; override `RELEASE_SET` only with another explicit lock. This operation does not bootstrap Bazel, invoke a compiler, check out source, install a runtime, or fall back to Actions artifacts. It verifies release/publisher/tag-object/peeled-commit identity, unique asset ID/name/size and GitHub's SHA-256 metadata before accepting downloaded bytes. Downloads stage on SSD; verified archives are retained once by digest on internal storage. An interrupted ingest is recorded as pending and can resume without re-downloading a complete copy; fresh and recovered copies both pass a file/directory durability barrier before sealing.

Online reuse rechecks remote identity and authenticates retained bytes. Explicit offline reuse makes no current revocation/publication claim:

```console
Tools/bazel/run.sh acquire-releases Tools/bazel/releases.lock.json --offline
```

The acquisition set pins devcontainer 1.0.1, Apple's signed Container 1.4.1 installer, the matched Container/Compose 0.15.1 archives, and Docker Compose 5.3.1's GitHub Darwin ARM64 binary. All five assets (429,532,407 bytes) were acquired and reused offline on 2026-09-16. This is **binary acquisition only**, not runtime qualification: `runtimeReady` and `signatureChecked` remain false. The guest/kernel/Engine closure, package trust checks and artifact-only fixture adapters remain required. The upstream Dev Containers CLI still has no GitHub release assets; its separately reviewed reference bundle is not silently replaced with a source build or a different distribution. The existing parity manifest is not upgraded by acquisition alone.

## Storage and evidence

| Data | Location / policy |
| --- | --- |
| Source and tracked design | Internal repository/worktree |
| SSD enrollment | `~/Library/Application Support/ContainerFamily/retained/workflow/ssd-volume.uuid` |
| Tool downloads, repository cache, action cache, Bazel outputs | `/Volumes/SSD/cf/bazel/` |
| Test scratch | Bazel-assigned `TEST_TMPDIR` under the enrolled SSD root |
| Per-invocation build events | Printed `invocations/run.*/events.json` on SSD |
| Long-term build/test evidence | `~/Library/Application Support/ContainerFamily/retained/workflow/bazel-evidence.sqlite`, deduplicated by SHA-256 in transactional records |
| Verified published binary archives | Internal `workflow/release-objects/<sha256>` with `release-inputs.sqlite` ingest/verification records |

On Darwin, Foundation can choose the system temporary directory before consulting `TMPDIR`. Test fixtures explicitly use `TEST_TMPDIR`; atomic product writes stage a private temporary sibling instead of using a volume-level replacement directory. This does **not** claim that all host-runtime writes have been audited. The launcher rejects broad `clean` and release commands and does not prune unrelated files, runtime instances or GitHub assets. Bazel's native idle cache maintenance limits the disposable disk cache to 30 GiB and 14 days.

`make bazel-cleanup` reports expired invocation scratch without deleting it. `Tools/bazel/run.sh cleanup --apply` removes only registered invocation files older than 14 days, after acquiring the same workspace lease as the launcher and authenticating their complete retained evidence. `--days N` explicitly changes that age. Cleanup does not recurse, follow links, adopt old unknown folders, delete incomplete/unretained evidence, remove reference archives or touch GitHub. A partially removed scratch set can resume while its ownership marker and complete retained bytes survive. Interrupted deletion after the last marker removal may leave an empty unowned folder for reporting rather than unsafe automatic adoption.

`make bazel-checkpoint` is the coherent local checkpoint: helper tests, stock unit coverage plus products, stock optimized archive, enhanced unit coverage plus products, enhanced optimized archive, then owned scratch cleanup. Use focused targets during editing; do not run this whole checkpoint after every change. It does not publish or replace exact-head remote authorities. GitHub branch/release cleanup and broader restored-output/cache lifetimes remain separate migration work.

A workspace lease covers the Bazel invocation and evidence retention, preventing the next launcher invocation from overwriting reports before they are retained. Retention uses a single SQLite transaction for the invocation and its referenced bytes, checks existing blob hashes, and rejects conflicting receipts. Both successful and failed completed invocations are retained. Incomplete events cannot be sealed. Source identities are captured before and after execution; a changed source commit or source file invalidates the run. Bazel's generated module lock is recorded on both sides but may be updated by dependency resolution. Profile selection is tracked by the reproducible module extension without rewriting the module lock on each stock/enhanced switch. Dirty development receipts are not immutable release authority. `query` receipts are explicitly diagnostic; `info` emits no build events and is not build evidence.

Bazel and the leased shell run with an explicit clean environment: inherited credentials, shell startup hooks and arbitrary PATH entries are not forwarded. Retention also rejects event streams containing non-allowlisted client environment keys. Earlier pre-isolation evidence can contain inherited credentials and must remain private, never uploaded raw; rotate any exposed credentials before reusing them. The build environment is not an isolation boundary for hostile source code: untrusted PR code must still run on a disposable worker without signing credentials.

The launcher automatically validates canonical unit and qualification coverage before returning success and writes `source-tests.json` or `qualification.json` beside its event file. To independently check a completed unit invocation before another build overwrites its output tree:

```console
python3 Tools/bazel/check_evidence.py --suite source --profile enhanced /Volumes/SSD/cf/bazel/invocations/run.EXAMPLE/events.json
```

Replace the example path with the emitted location. `--warm` additionally requires every test to have been cached. The validator rejects missing targets, failed/incomplete builds, retries, missing/skipped test cases and empty coverage, and requires positive hits in mandatory executable source files. Declaration-only files can legitimately have zero measurable lines and are not selected as execution probes. It reads evidence only and never launches or retries work. Native Bazel action statistics in the event file provide separate compile/cache evidence.

## Qualification evidence

Validated locally on 2026-09-16 with Xcode 27.0 build 27A266a:

- All three native executables build for stock and enhanced dependencies.
- Ten source test targets pass for each profile: 221 stock and 232 enhanced cases. Unit-only measured coverage is 11,629 / 13,448 lines for stock (invocation `fd4215cd-be92-47e4-a13a-c444d369f1e4`) and 11,800 / 13,644 for enhanced (invocation `e8973c08-c496-4b4c-a4d6-6716710f173e`). This remains below 90% and is not release acceptance or a replacement Sonar result.
- An unchanged enhanced coverage invocation reused all ten test results and executed no tests or Swift compilations. Bazel reported 0.461 seconds; this is a build-cache diagnostic, not a quiet-machine runtime benchmark. Its retained invocation is `f0535931-8361-4834-8396-9b1ea6b179d7`.
- The earlier Model/State qualification passed ASan and TSan. Full-graph sanitizer and leak proof remains outstanding; earlier subset evidence must not be represented as full-product proof.
- The first enhanced optimized archive built natively in 156.175 seconds (invocation `776cbcfc-08b2-4580-bc1e-32d2533c31f7`), was retained, restored and its CLI executed successfully. The unchanged request took 0.361 seconds with zero compilation/archive execution (invocation `410cb60d-e28a-4530-bce5-3824631d01b7`). These are cache diagnostics, not quiet-host runtime comparisons.
- The complete `make bazel-checkpoint` passed at `db772e52f573ee9387cf9660d597c969eecd68aa`: 54 helper tests, stock coverage/products (`35964129-0478-4d39-9e25-00ccd819dc44`), stock optimized archive (`4b366505-0939-47f0-ae4e-1feb95d7d9b3`), enhanced coverage/products (`c80ce02b-0ecb-4fea-89aa-95e50290fb46`), enhanced optimized archive (`25947ef4-ed45-4af4-9e9d-0ec1e2e2f254`) and owned cleanup. Cleanup preserved recent/unknown scratch; it made no material deletion. Later source edits require affected checks and are not qualified by this historical checkpoint.

## Pinned compatibility adaptations

The small maintained `rules_swift` patch makes test-source writes report errors, avoids Foundation atomic replacement outside the SSD sandbox for disposable generated/XML outputs, and registers Bazel's LCOV merger. The project version generator instead retains atomic publication using a temporary sibling and rename; regression tests cover invalid inputs, unchanged contents and failed output publication.

The test wrapper exports LLVM profiles from the same test execution, without a second test run. Its intermediate `.profdata` stays outside the LCOV merger's input directory because Bazel rejects mixed profile/LCOV inputs. Bazel then filters and merges LCOV against its declared source manifest. Framework-only probes legitimately have no instrumented production sources. No tests are excluded to inflate the reported percentage.

References: [rules_swift](https://github.com/bazelbuild/rules_swift), [Bazel caching](https://bazel.build/remote/caching), [Build Event Protocol](https://bazel.build/remote/bep), [Bazel coverage collector](https://github.com/bazelbuild/bazel/blob/8.8.0/tools/test/collect_coverage.sh).

## Remaining migration gates

1. Qualify uncached host integration, manifest/graph drift checks and immutable release input snapshots; propagate shared native rules to the other family repositories.
2. Meet native coverage acceptance against the actual maintained scope, full sanitizers/leak harnesses, and current Sonar reporting.
3. Complete the runtime closure, reference-bundle distribution and artifact-only adapters around the verified GitHub acquisition path. Reference products must not be rebuilt. Keep real runtime tests and fresh quiet-host benchmarks outside stale test-cache reuse.
4. Implement durable release checkpoints, exact artifact identity, preflight authorization, signing/notary resume, immutable publication and Homebrew verification as thin non-cacheable host operations.
5. Finish broader SSD/local/GitHub ownership cleanup and release-operation recovery. Internal evidence/candidate retention, authenticated candidate restore, verified reference reuse, owned invocation cleanup and repeated native-request deduplication are implemented; this is not yet the complete recovery system.

Until these gates pass, do not use this subset to promote a release, assert full parity, or replace protected CI checks.
