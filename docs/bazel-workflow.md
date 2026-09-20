# Native Bazel workflow

## Status and scope

This is an opt-in native product build, not yet the replacement release system. Bazel owns native Swift/C compilation, the complete pinned package graph, generated version inputs, C/SQLite interoperability, test discovery and action caching. The native products are `devcontainer`, `devcontainer-compose`, `devcontainer-engine` and `devcontainer-docker`. Existing SwiftPM/CI/release entry points remain unchanged until the host and release operations are qualified. There is no new Python build coordinator: the small Python helpers validate and retain evidence; they never schedule or retry builds.

`//:unit` runs all ten deterministic source test targets for either profile. Exact discovered counts and coverage belong to each retained invocation's XML and report, not a fixed historical count in this guide. `//:bazel_qualification` remains a smaller compiler/framework/generator diagnostic and must not substitute for the unit suite. Native executable modules are compiled once and shared with their importing tests. Third-party packages retain their declared Swift language settings; Swift 6 and warnings-as-errors apply to project code.

The product-owned `Tools/bazel/evidence-policy.json` uses the shared validator's existing schema 1. It retains all ten unit targets and raises discovery minima for the bounded CLI, Compose and process regressions, requiring execution in their production sources. Consult that file for current per-target floors; they are not a total test-count or coverage claim. Consumer inventory updates therefore do not change shared executable tooling or weaken the 90% coverage gate. Historical receipts without this policy remain historical; they cannot satisfy a current policy-bound gate.

## Run locally

### Unattended authorization requirement

Routine build, test, benchmark and release runs must not wait for an interactive approval. Required macOS permissions, Developer ID access and notarization credentials belong in an explicit one-time setup phase, using stable signed executable identities. The launcher forces Git/SSH credential prompting off and excludes inherited credential helpers. Disposable test Keychains use Security.framework with user interaction disabled; the helper and selected API readiness probe have bounded deadlines and retain private diagnostics on failure.

This is not yet a claim that the entire release workflow is prompt-free. Host permission/identity preflight, signing/notarization integration and unattended recovery still need end-to-end qualification. Missing authorization must stop before expensive work, preserve recoverable state and identify the setup action needed. Operating-system updates, replaced signing identities or revoked permissions can require new operator consent; never bypass that consent, disable platform security, or approve a dialog automatically.

Local-network authorization must be checked for the responsible runtime, not inferred from a successful connection by the coordinator or another executable. The [enhanced port diagnostic](evidence/enhanced-workspaces-20260920.md#separate-port-diagnosis) proves why: direct host access succeeded while the selected native publisher received `EHOSTUNREACH`. After the operator reported pressing Allow, the unchanged complete port fixture passed with the same binaries; the exact dialog/policy cause was not independently identified. Keep downloaded runtime identities stable, reserve consent for explicit setup, and retain a bounded runtime-context diagnostic when effective access cannot be established. Do not toggle ambiguous duplicate System Settings entries. The legacy `Tools/release/sign-and-notarize.sh` still invokes signing/notarization without the qualified noninteractive preflight; it is not the unattended Bazel release path.

### Commands

Requirements: Apple silicon, full Xcode (validated with Xcode 27 / Swift 6.4), an external volume mounted at `/Volumes/SSD`, Python 3.9 or newer for helper tests, and network access for the first verified tool/dependency download.

```console
make bazel-configure
make bazel-test-tools
make bazel-build
make bazel-unit
make bazel-coverage-report INVOCATION=RETAINED-UNIT-INVOCATION-ID
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

Bazel 8.8.0 and dependency versions are pinned. The launcher downloads Bazel directly and verifies its SHA-256 on every invocation, including cache hits; it does not delegate through Bazelisk or a workspace wrapper. `Package.resolved` and `Package.stock.resolved` supply exact source revisions through the native Swift package rules. Select one profile with `--config=stock` or `--config=enhanced`; independent dependency overrides and mismatched profile defines are rejected. Makefile remains the product version authority. With `--config=release`, the launcher supplies declared `DEVCONTAINER_COMMIT` and `DEVCONTAINER_BUILD_LANE` defines from the captured invocation source SHA and the `candidate` lane; caller identity overrides are rejected. Ordinary development defaults explicitly to `unspecified`/`development`. Candidate packaging rejects an unspecified commit, and runtime admission additionally requires clean unchanged source evidence. Indirect flag/target-pattern files are rejected so suite selection cannot bypass evidence validation.

The Keychain-backed service process tests and live Apple-service logging test are explicit, uncached host-integration targets, not silently skipped unit cases. They require `DEVCONTAINER_HOST_INTEGRATION=1`; the launcher preserves only a validated `0` or `1` for that opt-in. The logging target is incompatible with the stock profile because that upstream API is absent. These targets remain unqualified for unattended execution and are not a parity claim.

CLI/process/service component coverage includes bounded Doctor probes, invalid-input admission, launcher error cleanup, descendant pipe closure, single-use reaping and a real controlling-terminal fixture. The process test helper is declared as a test-only executable/runfile and runs without containers or Keychain. Lifecycle fakes prove startup/waiter/cancellation cleanup ordering, not real service admission or complete bootstrap recovery. See [PR 83](PR-83.md) for retained red/green observations and the unchanged release gates.

### Reuse native coverage

`make bazel-coverage-report INVOCATION=ID` exports the retained unit invocation's exact LCOV, Sonar generic `coverage.xml` and digest-bearing `receipt.json` under the SSD coverage directory. It does not start Bazel, compile, rerun tests or contact Sonar. It works after the original test output disappears. Only successful, complete source-unit coverage with clean recorded source identities is eligible; dirty, mismatched, corrupted or incomplete evidence fails. The XML preserves the LCOV line denominator without additional exclusions, and includes uncovered lines.

The receipt names the original source commit and profile, not the current checkout. A historical export must not be submitted as current-head coverage; scanner integration must verify that binding before upload. This is coverage export, not a passing 90% quality gate, hosted analysis or release authority. Existing `make sonar-scan` has not been switched over. Report output is disposable and reconstructible; broader report-directory lifetime cleanup remains a migration gate.

Use `Tools/bazel/run.sh coverage-report ID --minimum-percent 90` to apply the line-coverage threshold to an already retained report, without compiling or rerunning anything. Select stock explicitly with `--config=stock`; enhanced is the default. Unlike export-only, the gate requires a clean checkout and the same current source SHA, profile and consumer policy. Another repository/profile or historical head cannot satisfy it. The comparison uses raw counts rather than a rounded percentage. A below-target result fails but still leaves the authenticated diagnostic report on SSD; it does not change the historical test result or grant release authority.

Consumers such as Compose supply `Tools/bazel/evidence-policy.json` with explicit stock/enhanced target discovery minima, required executed production files and source roots. The shared validator checks canonical aggregate `test` invocations as well as `coverage` invocations. Plain test results make no coverage claim. Coverage export binds the consumer policy digest to the recorded source identity and accepts only its declared production trees, including Compose's Go helpers. Missing/skipped cases, incomplete inventories, malformed line counts and missing source probes fail closed. Consumers do not maintain another validator or scheduler.

Schema 1 remains unit-only. Schema 2 additionally declares `component_scope` and a disjoint `component_tests` map for each profile; it cannot replace any unit target or discovery minimum. The named `coverage_stock` / `coverage_enhanced` aggregates select the exact union as inventory `unit-cli`, while existing unit aggregates retain inventory `unit`. Validation rejects extra or missing tests. Receipts preserve the inventory and truthful scope; a combined quality gate must explicitly use `--inventory=unit-cli`. Historical unit receipts retain their original bytes and mean `unit` when no identifier exists. Export-only never relabels evidence and rejects an inventory-selection option without a threshold. This is no-runtime CLI component coverage, not live runtime or Linux guest coverage.

## Build timing records

Every new build, test and coverage invocation records monotonic elapsed time around the Bazel process, its exit status, before/after load averages, machine identity hash, macOS/Xcode/Swift versions and configuration fingerprint. Metadata collection and evidence retention are excluded from that elapsed interval. Retention seals these measurements with the source identity, Bazel version, action/cache metrics and each test's original duration/cache disposition. Cached test durations describe their original execution, not time spent rerunning a cached test.

Use `make bazel-build-timings INVOCATION=ID` to read the record, or add `BASELINE=ID` for a comparison. Different platforms, toolchains, configurations, profiles or target sets are reported as incompatible; failed or dirty runs receive no ratio. Reports contain no inherited environment or raw historic events. Old runs without measurements remain unavailable rather than acquiring invented times. Measurements survive disposable SSD output cleanup through internal retention.

Ordinary invocation records are explicitly non-authoritative observations: low load averages alone do not prove an isolated machine. Controlled cold, warm, no-change and uncached-test campaigns must record cache preparation and quiet-host checks separately before performance claims are accepted. Neither recording nor reporting schedules another build or changes the existing correctness gates.

## Native candidate archives

Configuration command regressions are covered directly through the real argument parser and command execution in `DevContainerCLITests`, without runtime services. The candidate preserves omitted strictness, explicitly changes it only with `--strict`/`--no-strict`, and keeps new files strict. Eight additional methods cover independent backend/frontend selection, private output, invalid-input preservation and durable claim/reset/conflict behavior. The original reset bug fails at `b142c517-dc04-437b-8caa-40bdb4f6f4c5`; the complete 21-method CLI target passes stock `3bfa14f5-4039-4d98-808d-8e7244744a9c` and enhanced `2b044b18-8357-431e-8ec6-c001fa952b7a`. These development observations do not qualify the packaged CLI, live runtime or an overall 90% gate.

`make bazel-package` requests optimized native products and creates `//:candidate_archive` without invoking SwiftPM or another build. It also runs `//Tools/bazel:package_smoke` against the actual archive, not binaries from the developer build tree. Both targets are explicit so the same retained invocation contains archive/receipt bytes and smoke evidence. The archive contains four arm64 executables, plugin layout, launchd template, project legal files, gathered compiled-dependency license texts, the selected dependency lock and a digest-bearing candidate identity. The private `libexec/devcontainer/reference` directory contains prebuilt Node 24.21.0, official Dev Containers CLI 0.88.0, their licences/notices and the exact source lock. Bazel downloads only the reviewed official archives with exact checksum pins; no npm hooks, global installation or reference source compilation runs. Receipt schema 2 binds all private files and the lock to captured source; its versioned admission layout preserves existing schema-1 preparation identities. It reuses the repository's deterministic archive writer. This is an **unsigned candidate**, explicitly marked `distributionReady: false`, not a signed release or a replacement for the complete release SBOM/notices/signing gate. Nested Node signing/entitlements and live D01 remain separate gates. The release configuration binds candidate identity to the launcher's captured source commit.

The package harness has 13 cases, including artifact/CLI checks and two cleanup regressions, all individually timed in JUnit. Before running any packaged executable, it checks the exact regular-file/directory inventory, modes, bounded sizes, ARM64 headers, dependency-lock, private-runtime and product hashes, identical plugin bytes and static candidate identity. The provenance case then executes the CLI and compares its reported version/commit with that identity. Commands use a private SSD home, clean environment and runtime/Docker traps. The real private Node/CLI executes configuration reading through the project frontend against an isolated empty-inventory socket, plus plugin help; this is not a live runtime. The Compose bridge invokes only an explicit harmless native-provider fixture; this proves handoff, not a complete Compose installation or a Docker-free default-provider policy. Service help never starts the Engine. The process-session cleanup helper is reused byte-for-byte from Container Compose's `Tools/bazel/cli_process.py` at `0cc4c6890c937cfb58285bc8e1ca00e4c9b1dc09`, retaining its Apache notice. An uncertain child preserves both its home/logs and the extracted package; regressions cover preservation and verified cleanup. This is local unsigned package verification, not Homebrew, Gatekeeper or notarization qualification.

Development invocation `9798f50d-6503-4516-aacd-776dd211dcd2` passes all nine stock cases (4.415 seconds build/test; 1.9 seconds target), and `restore-candidate` authenticates and restores the archive/receipt from that same invocation without compilation. The preceding `e2b92778-2bd6-4522-a56b-67d20e029099` failed test discovery because a source-directory name collided with the executable runfile; the directory is renamed. Independent review also caught unconditional package cleanup and missing explicit archive retention; both are corrected with negative cleanup tests and actual restoration proof. These development timings are not quiet benchmarks or final release acceptance.

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
| Verified reusable release executable trees | `~/Library/Application Support/ContainerFamily/retained/workflow/prepared-releases/` |
| Test scratch | Bazel-assigned `TEST_TMPDIR` under the enrolled SSD root |
| Per-invocation build events | Printed `invocations/run.*/events.json` on SSD |
| Long-term build/test evidence | `~/Library/Application Support/ContainerFamily/retained/workflow/bazel-evidence.sqlite`, deduplicated by SHA-256 in transactional records |
| Verified published binary archives | Internal `workflow/release-objects/<sha256>` with `release-inputs.sqlite` ingest/verification records |

Test scratch is namespaced by the canonical workspace path's 12-character SHA-256 prefix before Bazel adds its target hash. Identically named tests in separate repositories therefore do not clear each other's scratch. The short namespace preserves Darwin Unix-socket path capacity; the launcher regression checks stable reuse, distinct workspace paths, and a representative socket length. Workspace leases still serialize invocations in the same checkout. The original Compose package-smoke failure `4b88135e-7497-4606-b17f-3d5063b97299` occurred while the devcontainer package smoke used the same unnamespaced target directory; its failure remains retained, not retried into a passing history.

The service-provider integration fixture uses the shorter owned directory prefix `dcp-` so its actual `provider.sock` path also remains below Darwin's 104-byte bound. The launcher regression covers both that provider socket and the frontend's `engine.sock`; production socket selection is unchanged.

On Darwin, Foundation can choose the system temporary directory before consulting `TMPDIR`. Test fixtures explicitly use `TEST_TMPDIR`; atomic product writes stage a private temporary sibling instead of using a volume-level replacement directory. This does **not** claim that all host-runtime writes have been audited. The launcher rejects broad `clean` and release commands and does not prune unrelated files, runtime instances or GitHub assets. Bazel's native idle cache maintenance limits the disposable disk cache to 30 GiB and 14 days.

`make bazel-cleanup` reports expired invocation scratch without deleting it. `Tools/bazel/run.sh cleanup --apply` removes only registered invocation files older than 14 days, after acquiring the same workspace lease as the launcher and authenticating their complete retained evidence. `--days N` explicitly changes that age. Cleanup does not recurse, follow links, adopt old unknown folders, delete incomplete/unretained evidence, remove reference archives or touch GitHub. A partially removed scratch set can resume while its ownership marker and complete retained bytes survive. Interrupted deletion after the last marker removal may leave an empty unowned folder for reporting rather than unsafe automatic adoption.

`make bazel-checkpoint` is the coherent local checkpoint: helper tests, stock unit coverage plus products, stock optimized archive/smoke, enhanced unit coverage plus products, enhanced optimized archive/smoke, then owned scratch cleanup. Use focused targets during editing; do not run this whole checkpoint after every change. It does not publish or replace exact-head remote authorities. GitHub branch/release cleanup and broader restored-output/cache lifetimes remain separate migration work.

A workspace lease covers the Bazel invocation and evidence retention, preventing the next launcher invocation from overwriting reports before they are retained. Retention uses a single SQLite transaction for the invocation and its referenced bytes, checks existing blob hashes, and rejects conflicting receipts. Both successful and failed completed invocations are retained. Incomplete events cannot be sealed. Source identities are captured before and after execution; a changed source commit or source file invalidates the run. Bazel's generated module lock is recorded on both sides but may be updated by dependency resolution. Profile selection is tracked by the reproducible module extension without rewriting the module lock on each stock/enhanced switch. Dirty development receipts are not immutable release authority. `query` receipts are explicitly diagnostic; `info` emits no build events and is not build evidence.

Bazel and the leased shell run with an explicit clean environment: inherited credentials, shell startup hooks and arbitrary PATH entries are not forwarded. Retention also rejects event streams containing non-allowlisted client environment keys. Earlier pre-isolation evidence can contain inherited credentials and must remain private, never uploaded raw; rotate any exposed credentials before reusing them. The build environment is not an isolation boundary for hostile source code: untrusted PR code must still run on a disposable worker without signing credentials.

The launcher automatically validates canonical unit and qualification test/coverage aggregates before returning success and writes `source-tests.json` or `qualification.json` beside its event file. To independently check a completed unit coverage invocation before another build overwrites its output tree:

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
- After preserving hosts staging permissions, the complete checkpoint passed at `50f74fa482843ca44203b4a3154c50d5ac644d4e`: stock unit/products `452e63d3-9386-47f6-9c13-6ab2aa965eb9`, stock archive `c090335b-f04f-4e99-9860-6a335f12ee4f`, enhanced unit/products `898ae68e-849d-4688-9ca9-533dc0e860b9`, enhanced archive `6f20ac12-729a-4a7d-bd56-cc1fe8884760`. The later report exporter successfully consumed both retained unit receipts without rebuilding. Enhanced coverage was 11,815 / 13,646 lines (86.5821%); this remains unit-only evidence below the quality target. Sixty helper tests pass, including six coverage conversion, provenance, corruption and reuse regressions.

## Pinned compatibility adaptations

The small maintained `rules_swift` patch makes test-source writes report errors, avoids Foundation atomic replacement outside the SSD sandbox for disposable generated/XML outputs, and registers Bazel's LCOV merger. The project version generator instead retains atomic publication using a temporary sibling and rename; regression tests cover invalid inputs, unchanged contents and failed output publication.

The test wrapper exports LLVM profiles from the same test execution, without a second test run. Its intermediate `.profdata` stays outside the LCOV merger's input directory because Bazel rejects mixed profile/LCOV inputs. Bazel then filters and merges LCOV against its declared source manifest. Framework-only probes legitimately have no instrumented production sources. No tests are excluded to inflate the reported percentage.

References: [rules_swift](https://github.com/bazelbuild/rules_swift), [Bazel caching](https://bazel.build/remote/caching), [Build Event Protocol](https://bazel.build/remote/bep), [Bazel coverage collector](https://github.com/bazelbuild/bazel/blob/8.8.0/tools/test/collect_coverage.sh).

## Remaining migration gates

### Native documentation

Documentation generation requires a clean, committed source checkpoint. The launcher compares captured file bytes, Git modes and inventory directly with the commit tree, includes ignored files beneath `Sources`, and requires direct symlink targets to be committed; indirect links cannot attest a clean checkpoint. This does not trust status output or index flags that can hide edits. It forbids caller overrides, and the DocC rule rejects dirty or unknown state during analysis before running any compilation/conversion action. Other development build/test targets continue to permit dirty worktrees with retained dirty-source evidence.

`make bazel-docs BAZEL_PROFILE=stock` (or `enhanced`) builds and tests `//:documentation` using the existing optimized Bazel modules. There is no SwiftPM invocation, dependency resolution, runtime startup or publication in this target. Each of the nine public modules has a separately cached symbol extraction and DocC conversion; the Core catalog resolves its cross-module links against the other eight archives, then DocC merges the results. Warnings fail the action. The generated site keeps DocC's application shell, static routes, search assets and a source-commit/module inventory. Optimized module symbol graphs currently omit source locations, so the revision is recorded but per-symbol GitHub source links are not promised.

The site stays in the SSD Bazel output tree until the final documentation publication phase. DocC preserves sandbox input symlinks when copying catalog resources and merging archives; the adapter materializes only links to declared inputs and rejects other links, so the output is standalone and never writes back into cached inputs. Nine fault/argument tests and three real-site checks cover source identity, module inventory, bounded tool failure, input immutability, catalog resources, static routes and absence of local host paths. The identical action/rule/test snapshot is reused by Compose; its existing shared launcher lock is unchanged. The legacy public documentation workflow remains in place until the complete migration is reviewed and admitted.

### Trusted workflow qualification, 18 September 2026

The private controller is merged through PR 8. Owner-dispatched GitHub runs passed both devcontainer profiles ([stock](https://github.com/stephenlclarke/container-build/actions/runs/35368867084), [enhanced](https://github.com/stephenlclarke/container-build/actions/runs/35369006231)) and both Compose profiles ([stock](https://github.com/stephenlclarke/container-build/actions/runs/35369122230), [enhanced](https://github.com/stephenlclarke/container-build/actions/runs/35369312307)). Each ran native coverage, component contracts, optimized package checks, the unchanged 90% gate and owned expired-invocation cleanup. The source pins were devcontainer `8e12ed726d73480152236a07598df75a4dca16ae` and Compose `4b9f1fc8b364ff6f882140fba9672c1c2c1d02b9`; these results do not qualify subsequent edits automatically. Unattended [Compose Sonar](https://github.com/stephenlclarke/container-build/actions/runs/35368647237) and [devcontainer Sonar](https://github.com/stephenlclarke/container-build/actions/runs/35369801683) also passed, reusing authenticated paired retained coverage reports without rebuilding. Full live parity, quiet-host performance evidence, signing/notary resume and release publication remain separate unfinished gates.

Runner provisioning was approved on 17 September 2026. The dedicated trusted lane is established in private `stephenlclarke/container-build`. Public fork workflows cannot select that private registration; reviewed source pins and owner-only manual dispatch are required. This does not yet replace the public protected checks or claim OS-account isolation.

1. Qualify uncached host integration, manifest/graph drift checks and immutable release input snapshots; propagate shared native rules to the other family repositories.
2. Meet native coverage acceptance against the actual maintained scope, full sanitizers/leak harnesses, and current Sonar reporting.
3. Complete the runtime closure, reference-bundle distribution and artifact-only adapters around the verified GitHub acquisition path. Reference products must not be rebuilt. Keep real runtime tests and fresh quiet-host benchmarks outside stale test-cache reuse.
4. Implement durable release checkpoints, exact artifact identity, preflight authorization, signing/notary resume, immutable publication and Homebrew verification as thin non-cacheable host operations.
5. Finish broader SSD/local/GitHub ownership cleanup and release-operation recovery. Internal evidence/candidate retention, authenticated candidate restore, verified reference reuse, owned invocation cleanup and repeated native-request deduplication are implemented; this is not yet the complete recovery system.

Until these gates pass, do not use this subset to promote a release, assert full parity, or replace protected CI checks.

## Final delivery and public evidence

The required outcome is the complete recoverable Bazel build system and new stable GitHub/Homebrew releases of both `container-compose` and `devcontainer`, not merely successful native compilation. Required pre-publication gates still apply. After publication, download and authenticate those actual release assets and run the full unit, integration and parity cycle; do not rebuild reference releases or silently substitute local binaries for published products.

To demonstrate actual improvement, also compare each newly published stable product with its previous stable release on the same host and workloads. Pin release IDs, asset IDs and checksums before measurement. Use repeated paired, counterbalanced samples with the same declared cold/warm state; report old/new median and P95, absolute seconds saved, percentage reduction in latency and raw trial order. Distinguish measured improvements from regressions and differences within the declared noise band. An older release that cannot complete a workload provides a functional-compatibility difference, not a timing denominator or an infinite-speedup claim. A locally built candidate remains candidate-only evidence until the published bytes are downloaded and authenticated. Do not select only favourable fixtures or substitute cached build timings for runtime results.

Retain and publish build benchmarks separately from runtime comparisons. Runtime comparisons cover devcontainer versus real Docker devcontainers, enhanced Container versus stock Apple Container, and container-compose versus Docker Compose. Collect quiet-host evidence, exact source/release/toolchain/image identities, declared cold/warm conditions, repetitions, monotonic setup/operation/cleanup durations, median/P95, raw per-fixture results, failures and cleanup outcomes. Ordinary slowdowns below 10x are informational; hangs, timeouts and ratios of at least 10x fail the corresponding parity timing. No video capture, compilation, scanning or other benchmark may overlap timed runtime samples. Generate reports from retained machine-readable evidence, publish sanitized evidence/checksums with the matching release, and link it from the documentation. Private service snapshots, credentials and unfiltered host logs must never enter public evidence.

The final documentation phase generates and publishes both DocC sites, benchmark reports and live VHS demos, then checks README badges, statistics, manuals, installation instructions and conformance notes against the released versions. A passing render is not proof of correct demo commands: retain the live command outcomes and visually inspect the recordings.

Reuse the original `Tools/release/record-vhs-live-demo.sh` and tape definitions as the starting point. The existing devcontainer recorder rebuilds with SwiftPM, uses `/private/tmp`, requires the Docker CLI and waits for a hard-coded `1.0.0`; these are migration gaps, not the accepted final recorder. The existing Compose tape starts only `nginx` and `alertmanager`. Its replacement must demonstrate installation and the entire default monitoring stack from `examples/monitoring-stack/docker-compose.yaml`, not only those two services. Do not enable its Linux-host-only profile on macOS.

The Compose recording must visibly show installation and versions, no containers in the isolated demo runtime, full-stack startup, container/status listings and representative command output, stopping the containers, stopped-state output, restarting the stack, restored running/health output, and verified final cleanup. The devcontainer recording must show installation, versions, workspace startup, useful in-container examples and verified cleanup without requiring a Docker installation. Use the downloaded stable assets and isolated demo resources; do not uninstall the operator's tools or remove unrelated containers to stage an empty screen. Temporary recording files belong on the enrolled SSD; final videos, tapes, reports and provenance are retained internally and published through the existing release/Pages paths.
