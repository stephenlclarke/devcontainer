# Software quality and delivery gates

## Status

This document is the quality policy and delivery scorecard for `devcontainer`.
The repository now contains the hosted CI, 90% coverage enforcement, Sonar
coverage export and quality-gate workflow, sanitizer jobs, CodeQL, parity
harness, DocC Pages workflow, deterministic package/SBOM tooling, and Homebrew
formula validation described below. At this development-candidate snapshot,
90 Swift tests pass with 90.05% first-party executable-line coverage. Full
isolated stock Apple runtime and live VS Code release evidence remains
outstanding, so these implemented gates are not yet a stable-support claim.

The policy turns the architecture in [`DESIGN.md`](DESIGN.md) and test design in [`TESTING.md`](TESTING.md) into measurable merge and release conditions. A stable release cannot replace a failed gate with a manual assertion.

## Quality principles

- Compatibility is an evidence-backed behavior claim, not a successful build.
- The stock Apple and `container-compose` providers remain independently identified and tested.
- First-party executable source maintains at least 90% overall line coverage and 90% changed-code line coverage.
- New code has no unresolved blocker or critical security issue, no unreviewed security hotspot, and no sanitizer finding.
- Reproducible inputs, least-privilege automation, artifact provenance, and meaningful installation tests are release features.
- Required checks fail closed when evidence is missing, malformed, stale, or bound to a different commit.
- Functional parity differences, missing fixtures, ignored tests, and accepted signal-13 fallbacks cannot be waived for a stable release.

## Quality scorecard

| Dimension | Required measure | Pull request | Stable release |
| --- | --- | --- | --- |
| Build and tests | Swift build plus unit, contract, and hosted integration suites pass | Required | Required on exact candidate |
| Overall coverage | Line coverage of first-party executable Swift source | At least 90.00% | At least 90.00% |
| Changed coverage | Covered changed executable lines relative to merge base | At least 90.00% | At least 90.00% for candidate range |
| Differential parity | Semantic differences in claimed fixtures | Affected hosted/oracle fixtures | Zero across every required real lane |
| VS Code E2E | Pinned stable VS Code and Dev Containers extension | Not run on untrusted code | Full pass |
| Style | SwiftLint strict and SwiftFormat lint | No new violations beyond the checked-in debt baseline | Baseline retired; zero violations |
| Memory safety | Swift AddressSanitizer | Pass for relevant changes | Pass on exact candidate |
| Concurrency safety | Swift ThreadSanitizer | Nightly/dispatch | Pass on exact candidate |
| Static security | CodeQL Swift | Required when source changes | No open release-blocking alert |
| Sonar new code | Reliability, security, maintainability, coverage, duplication | Quality gate passes | Candidate analysis passes |
| Dependencies | Dependency review and pinned resolution | No disallowed addition | Reviewed lockfile and licenses |
| Supply chain | SBOM, checksums, signatures/attestations | Build artifacts only | Complete candidate-bound evidence |
| Packaging | Archive and Homebrew install/test | Package validation | Physical-runner installed smoke passes |

Percentages are calculated before display rounding. “Pass” means the tool exited successfully, executed non-zero applicable tests or analysis, and produced parseable evidence for the expected commit.

## Coverage policy

The required coverage gates are:

- 90% line coverage overall;
- 90% line coverage on changed executable lines.

The report includes first-party production libraries, executables, service code, provider adapters, migrations, diagnostics, and cleanup paths. Generated code, test support, fixtures, and third-party source may be excluded through a reviewed path list. A file is not excluded because it lowers the result.

Unit tests provide most deterministic branch and error-path execution. Docker
contract and hosted integration suites run instrumented product binaries and
merge their profiles with unit-test profiles using `llvm-profdata merge
-sparse`; `llvm-cov` then exports the combined first-party line coverage. The
hosted CLI coverage harness also runs the instrumented `devcontainer` and
`devcontainer-compose` products through version, context, configuration,
backend-state, doctor, Docker-provider, and container-compose-provider success
and failure paths using deterministic local fakes. External Docker, Apple
runtime, `container-compose`, VS Code, and extension code never count toward
the product percentage. Real parity and VS Code runs may add profiles when
instrumented execution is reliable, but their behavioral gates remain
independent of coverage.

The gate derives its numerator and denominator from the same unique executable
source-line segments written to SonarQube generic coverage XML. It does not use
LLVM's aggregate summary, which can count multiple executable regions on one
source line and therefore does not match Sonar's line metric.

Sonar coverage excludes only
`Sources/DevContainerCLI/DevContainerCommand.swift` and
`Sources/DevContainerCore/DevContainerProject.swift`: these files contain
compile-time command registration and immutable build-info forwarding but no
LLVM executable coverage regions. All command implementations and both CLI
products remain instrumented. The repository-owned gate and Sonar therefore
measure the same executable source rather than granting a broad CLI exclusion.

The coverage job currently produces a human-readable summary and SonarQube
generic coverage XML and fails on missing binaries, profiles, source paths, or
test execution. Changed-line annotations and LCOV export remain release-gate
work. Coverage collection uses two attempts and
`SWIFT_TEST_ACCEPT_SIGNAL_13=0`, because a SwiftPM signal-13 fallback can leave
incomplete profile data.

The gate initially applies to all implemented first-party source, even while the repository is small. If a platform-only path genuinely cannot run in hosted coverage, the design response is an injectable boundary and a deterministic fake, followed by live testing, rather than a broad exclusion.

## Test and parity gate

[`TESTING.md`](TESTING.md) defines unit, Docker wire contract, hosted integration, Docker oracle, stock Apple, `container-compose`, fault/concurrency, and VS Code E2E suites. Quality automation enforces these additional rules:

- Every test workflow verifies that at least one expected test executed; an empty filter is a failure.
- Quarantine, skip, expected-failure, and retry annotations are machine-inventoried and prohibited in release scope.
- Retries are permitted only for explicitly identified infrastructure/toolchain failures. All attempts and the final classification remain visible.
- Flaky product behavior is a product failure. Retrying a parity fixture cannot convert it into stable-release evidence.
- Any changed Docker endpoint, runtime capability, normalizer, fixture, or manifest rule requires its owning contract and parity tests.
- Stable release validation reads the checked-in parity manifest and fails until every scoped fixture is implemented.
- Raw and normalized recordings, pins, cleanup results, and digests must identify the exact candidate commit.

The required-check aggregator will fail if an upstream job is cancelled, skipped unexpectedly, or omitted by a path filter. It may report a deliberate documentation-only skip only when the classifier itself ran and the changed paths are included in the checked policy.

## Swift style and compiler discipline

Swift source will be formatted and linted across the entire first-party source and test tree:

```console
make lint
make format-check
```

SwiftLint scans only first-party `Sources` and `Tests`. The initial structural
findings are recorded in a portable checked-in baseline, so new violations
fail immediately while the large router/runtime types are split in subsequent
quality work. The baseline is technical-debt evidence, not a claim that the
repository has zero existing findings.

Swift 6.2 is the checked-in language and toolchain target; it is not permission
to float the compiler silently. SwiftLint and SwiftFormat are validated by the
hosted quality workflow. Generated and vendored directories may be excluded
explicitly, but new production directories are included by default.

The package will use Swift 6 language mode and strict concurrency where dependencies permit. Compiler warnings are treated as failures in CI. New use of `@unchecked Sendable`, `nonisolated(unsafe)`, force casts, force tries, or fatal termination in library paths requires a narrow review and a test that demonstrates the invariant.

Public API additions require DocC documentation and tests. Error messages exposed through the Docker contract require stable error-class assertions; snapshots alone do not replace semantic checks.

## Memory and concurrency gates

Swift AddressSanitizer and ThreadSanitizer use the same full-log retry harness interface currently used by `container-compose`: `Tools/ci/run-swift-test.sh`, `SWIFT_TEST_RESULT_LOG`, `SWIFT_TEST_ATTEMPTS`, `SWIFT_TEST_TAIL_LINES`, and `SWIFT_TEST_ACCEPT_SIGNAL_13`.

The implemented ASan invocation is:

```console
SWIFT_TEST_RESULT_LOG=.build/swift-asan.log \
  SWIFT_TEST_ATTEMPTS=2 \
  Tools/ci/run-swift-test.sh swift test --disable-automatic-resolution --sanitize=address --no-parallel
```

The implemented TSan invocation is:

```console
SWIFT_TEST_RESULT_LOG=.build/swift-tsan.log \
  SWIFT_TEST_ATTEMPTS=2 \
  Tools/ci/run-swift-test.sh swift test --disable-automatic-resolution --sanitize=thread --no-parallel
```

These are Swift's AddressSanitizer and ThreadSanitizer modes, not custom leak or race parsers. They run serially with separate build/cache fingerprints and retain `.build/swift-asan.log` or `.build/swift-tsan.log`.

The current `container-compose` quality workflow uses ASan for pull requests or manual dispatch and TSan nightly or by manual dispatch. Its harness retries only a logged `swiftpm-testing-helper` signal 13, defaults to two attempts, tails 200 lines on success, and defaults `SWIFT_TEST_ACCEPT_SIGNAL_13` to `1` when passing output exists without detected failure output. This project will reuse that precise retry/log mechanism, but the exact stable candidate sets `SWIFT_TEST_ACCEPT_SIGNAL_13=0`; accepted fallback output is not valid release evidence. ASan will run for relevant pull requests, and TSan will run nightly, manually, and for stable candidates.

Any sanitizer diagnostic fails the job. Suppressions require a pinned upstream issue, the narrowest stack match, expiry, and security review; a suppression cannot cover first-party product code for stable release.

## Static analysis and security

### CodeQL

The implemented `.github/workflows/codeql.yml` analyzes Swift on `macos-26`
using CodeQL's manual build mode so the database observes the actual SwiftPM
build. It runs on protected-main pushes, a schedule, and manual dispatch.
Workflow actions are pinned by full commit SHA.

The CodeQL job will:

1. Initialize the Swift language database.
2. Resolve dependencies from the lockfile without updating them.
3. Build all production targets and representative generated paths.
4. Run the security-and-quality query suites.
5. Upload SARIF and verify that analysis completed for the candidate commit.

A new high or critical CodeQL alert is release blocking. Lower-severity findings require disposition before stable release and may not be dismissed as “used in tests” when the path is reachable from production.

### SonarCloud

The implemented `sonar.yml` submits sources, tests, and generic coverage XML to
SonarCloud after the repository-owned 90% gate passes. The quality gate for new
code requires:

- at least 90% line coverage;
- at most 3% duplicated lines;
- no blocker or critical reliability, security, or maintainability issue;
- every security hotspot reviewed;
- no unresolved analysis failure or missing coverage import.

The SonarCloud project uses `main` as its real main branch and a project-level
30-day new-code definition. The workflow validates both remote invariants
before scanning so a newly created project cannot silently publish
`Not Computed` badges.

SonarCloud supplements the repository-owned coverage and lint checks. A passing Sonar gate cannot override an independent coverage, compiler, CodeQL, sanitizer, or parity failure.

### Dependency and license review

The planned `.github/workflows/dependency-review.yml` will run for pull requests that change `Package.swift`, `Package.resolved`, actions, scripts that install tools, or fixture dependencies. Swift Package Manager lockfiles are supported by GitHub's dependency graph and will remain checked in.

The review will:

- fail additions with moderate, high, or critical known vulnerabilities;
- deny licenses incompatible with Apache-2.0 distribution;
- flag git dependencies that are not immutable;
- verify that GitHub Actions use full commit SHAs;
- require an updated third-party notice and SBOM mapping when a distributed dependency changes.

Automated advisories are followed by a reproducible build and relevant regression tests. A dependency update does not bypass full parity merely because production source was unchanged.

### OpenSSF Scorecard

The planned weekly `.github/workflows/scorecard.yml` will publish SARIF with minimal permissions and pinned actions. The project targets an overall score of at least 8.0 and no regression in Branch-Protection, Code-Review, Dangerous-Workflow, Pinned-Dependencies, Token-Permissions, Packaging, and Vulnerabilities checks.

Scorecard is a trend and hardening signal, not a substitute for a concrete gate. A score below target creates release-blocking work unless the failing check is objectively inapplicable and that determination is documented.

## Workflow architecture

The implemented workflow split keeps fast feedback separate from privileged
live validation:

| Workflow | Environment | Responsibility |
| --- | --- | --- |
| `ci.yml` | Hosted `macos-26` | Build, unit, contract, hosted integration, 90% coverage gates, and package validation |
| `quality.yml` | Hosted `macos-26` | SwiftLint, SwiftFormat, ASan, scheduled/dispatch TSan |
| `codeql.yml` | Hosted `macos-26` | Manual-build Swift CodeQL |
| `sonar.yml` | Hosted `macos-26` | Coverage export and fail-closed SonarQube Cloud quality-gate analysis |
| `docs.yml` | Hosted `macos-26` plus GitHub Pages | DocC build, verification, and publication |
| `parity.yml` | Three dedicated physical Apple-silicon runners | CLI and pinned VS Code parity for Docker, stock Apple, and Apple Compose |
| `stable-release-gate.yml` | Hosted verifier plus live evidence | Candidate-bound required-check and evidence verification |
| `prebuilt-binaries.yml` | Hosted and trusted release runners | Immutable archives, checksums, SBOM, signing, notarization, and publication |
| `homebrew.yml` | Hosted validation plus physical install smoke | Formula update, audit, install, meaningful test, live smoke |

All workflows use explicit least-privilege `permissions`, pinned action SHAs, concurrency groups, timeouts, deterministic tool pins, dependency caching keyed by lockfiles/toolchains, and artifact names containing the candidate SHA. Scripts contain the substantial logic so it can be run and tested locally.

Path filtering is an optimization, not authority. Changes to workflow policy, dependency resolution, shared CI scripts, parity manifests, normalizers, packaging, or release verification run every affected downstream gate.

## Runner trust boundary

Hosted `macos-26` is appropriate for compilation, SwiftPM tests, coverage, style, CodeQL, and sanitizers. It is an ARM64 hosted virtual machine with limited memory and no supported nested Apple container runtime, so a green hosted job does not prove live compatibility.

Real runtime and VS Code tests use three provenance-specific physical
Apple-silicon runner labels: `devcontainer-docker`,
`devcontainer-apple-stock`, and `devcontainer-apple-compose`, in addition to
`self-hosted`, `macOS`, and `ARM64`. This prevents a job from replacing the
runtime distribution beneath another session. Jobs use isolated application
roots and runtime namespaces and prove cleanup.

Untrusted fork pull requests never execute on the self-hosted runner. A dispatcher may enqueue only an exact commit from protected `main`, a scheduled protected ref, or a maintainer-approved manual input that already passed hosted checks. The live workflow checks the commit's repository and ancestry again before checkout. Test jobs do not receive release or tap credentials.

Runner maintenance includes OS/toolchain pin records, clean workspace verification, available disk thresholds, orphan process/resource detection, log redaction tests, and periodic credential rotation. Runtime source checkouts remain outside Desktop and Documents.

## Branch protection and merge gates

Protected `main` will require:

- reviewed pull requests and resolved conversations;
- successful required-check aggregation for build, test, overall coverage, changed coverage, style, ASan where relevant, CodeQL, Sonar, dependency review, documentation, and package validation;
- current branch with no stale approval after material changes;
- signed or otherwise policy-verified commits where repository settings support it;
- no administrator bypass for ordinary delivery.

Live runtime jobs cannot safely run arbitrary pull-request code and therefore do not become a fork-triggered merge requirement. Instead, a merge queue or protected-main candidate is automatically held from promotion until candidate-bound live parity succeeds. A failure opens a corrective change; it never causes the same untested SHA to be released.

The repository will document exact required check names so a renamed workflow cannot silently stop enforcement.

## Stable release gate

`stable-release-gate.yml` is an evidence verifier, not a test rerun shortcut. For the exact candidate SHA it requires:

- hosted CI, unit, contract, integration, 90% overall line coverage, and 90% changed-line coverage;
- strict style, ASan, TSan, CodeQL, SonarCloud, dependency review, and documentation checks;
- every fixture in the release parity manifest implemented;
- zero semantic differences across the Docker oracle, stock Apple stable/main recording, and `container-compose` stable/main recording required by the candidate;
- pinned stable VS Code and Dev Containers extension E2E;
- complete cleanup and no ignored, expected-failure, accepted-signal-13, or manual-waiver result;
- reproducible archive validation, licenses, notices, SBOM, checksums, and provenance;
- Homebrew archive install and meaningful physical-runner smoke.

The verifier checks repository, workflow identity, commit SHA, check conclusion, artifact digest, pin manifest, and expiration. Evidence generated for another commit, a mutable upstream ref, or a rerun with altered workflow code is rejected.

Stable tags and releases are created only after the gate passes. The release job builds from the tagged commit with the reviewed workflow, verifies the tag target, and never rebuilds from an untrusted artifact upload.

## Supply-chain evidence

Each release will publish:

- immutable architecture-specific archives and SHA-256 checksums;
- an SPDX 2.3 SBOM covering distributed binaries and dependencies;
- build metadata with Swift, Xcode, macOS SDK, project, Apple runtime, Dev Containers, Docker, Compose, and fixture pins;
- Apache-2.0 license and updated notices;
- GitHub artifact attestations for archives, checksums, and SBOM;
- candidate-bound quality and parity evidence digests.

GitHub artifact attestations can support SLSA Build Level 2. The project will claim SLSA Build Level 3 only after the build uses a protected reusable hosted workflow meeting the isolation and parameter requirements and the generated provenance is independently verified. Documentation must not inflate the level based only on the presence of an attestation.

Release workflows separate build from publication. Publishing permissions are granted only to the final protected environment after digest verification and any configured human approval.

## Homebrew quality

The planned stable formula in `stephenlclarke/homebrew-tap` will use immutable release URLs and checksums, declare Apple silicon and macOS Tahoe requirements, and install only this project:

```ruby
depends_on arch: :arm64
depends_on macos: :tahoe
```

`container-compose` remains optional and independently installed. A future mutable `devcontainer-current` formula, if introduced, must have an explicit conflict/migration policy and cannot share stable release claims.

The tap workflow will run Ruby syntax, `brew style`, `brew audit`, fetch, install, and formula test on a clean hosted runner. The formula test must do more than print `--version` or `--help`: it will validate or render a minimal pinned `devcontainer.json` configuration and assert structured output without requiring a live runtime. A separate physical-runner smoke will install the bottled/released artifact through Homebrew, register it in an isolated root, run `doctor`, exercise a minimal real backend operation, and remove all resources.

The formula update verifies the release attestation, checksum, version, supported architecture, and source repository before opening or merging a tap change. Tap credentials are unavailable to build and test jobs.

## Implementable now and deferred infrastructure

The bootstrap repository can implement immediately:

- strict Swift build/unit checks and non-empty test validation;
- SwiftLint and SwiftFormat configuration;
- the `Tools/ci/run-swift-test.sh` retry/log harness;
- coverage collection, profile merge, LCOV/generic XML export, and both 90% checks;
- raw-socket contract and fake-runtime integration infrastructure as production code appears;
- hosted ASan, TSan, CodeQL, dependency review, Scorecard, documentation, and package validation workflows;
- deterministic pin, manifest, normalizer, and evidence schemas.

The following require infrastructure or credentials and must remain visibly unavailable rather than silently green:

- physical Apple-silicon live parity and VS Code runner registration;
- Docker/Apple/Compose image and tool caches on that runner;
- SonarCloud organization/project token and quality-gate configuration;
- protected GitHub environments, branch rules, release signing/attestation authority, and tap token;
- release-retention storage for raw parity and E2E evidence.

Stable release automation remains disabled until those dependencies exist and a dry run proves the trust boundary.

## Adoption sequence

1. Add hosted build, unit, lint, format, coverage, CodeQL, dependency review, and non-empty-check aggregation.
2. Add the tested sanitizer harness and ASan/TSan workflows with separate build caches.
3. Add contract/integration coverage and enforce 90% overall plus changed lines from the first production implementation.
4. Add exact dependency/pin manifests, parity evidence schemas, and candidate-bound verification.
5. Provision and harden the physical runner, then enable stock Apple and `container-compose` live lanes.
6. Add pinned stable VS Code E2E, Homebrew live smoke, and scheduled main/Insiders forward-compatibility checks.
7. Enable stable tagging and publication only after a full candidate dry run satisfies every gate.

## Reference implementations and primary sources

The current sibling repositories provide implementation precedents, not proof that this repository already has the controls:

- `container-compose/.github/workflows/quality.yml` and `container-compose/Tools/ci/run-swift-test.sh` for the ASan/TSan retry and log harness described above;
- `container-compose/.github/workflows/ci.yml` for Swift coverage export and Sonar integration;
- `container-compose/.github/workflows/prebuilt-binaries.yml`, `stable-release-gate.yml`, and `homebrew.yml` for candidate-bound release and tap patterns;
- `container-k8s/.github/workflows/codeql.yml` for a Swift manual-build CodeQL pattern;
- `mac-sync/.github/workflows/prebuilt-binaries.yml` and `homebrew-tap/.github/workflows/homebrew.yml` for packaging and formula validation patterns.

Primary references:

- [Swift Package Manager test and coverage](https://docs.swift.org/swiftpm/documentation/packagemanagerdocs/swifttest/)
- [SwiftLint](https://github.com/realm/SwiftLint)
- [SwiftFormat](https://github.com/nicklockwood/SwiftFormat)
- [CodeQL build modes for compiled languages](https://docs.github.com/en/code-security/reference/code-scanning/codeql/build-options-for-compiled-languages)
- [SonarQube Cloud Swift analysis](https://docs.sonarsource.com/sonarqube-cloud/advanced-setup/languages/swift)
- [SonarQube generic test and coverage data](https://docs.sonarsource.com/sonarqube-cloud/enriching/test-coverage/generic-test-data)
- [GitHub dependency graph ecosystems](https://docs.github.com/en/enterprise-cloud@latest/code-security/reference/supply-chain-security/dependency-graph-supported-package-ecosystems)
- [Dependency review action](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/manage-your-dependency-security/configure-dependency-review-action)
- [OpenSSF Scorecard action](https://github.com/ossf/scorecard-action)
- [GitHub artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
- [Increasing a build's SLSA level](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/increase-security-rating)
- [Creating and maintaining a Homebrew tap](https://docs.brew.sh/How-to-Create-and-Maintain-a-Tap)
- [Homebrew formula testing](https://docs.brew.sh/rubydoc/Formula)
