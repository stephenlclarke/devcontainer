# Testing strategy

## Status

The repository contains the production Docker compatibility service, stock
Apple runtime adapter, optional `container-compose` provider, differential
parity harness, sanitizer workflows, and a pinned real VS Code end-to-end
driver. The hosted-safe suite contains 127 Swift tests and records greater than
90% first-party line coverage. For version 1.0.0, real Docker, stock Apple,
and separately identified `container-compose` lanes pass all 18 CLI fixtures
and the pinned real VS Code fixture with zero normalized semantic differences
and no performance failures. In the exact 1.0.0 tag run, the largest CLI ratios
are 2.876x for stock Apple and 4.509x for `container-compose`; the corresponding
VS Code ratios are 1.232x and 1.311x. Three-run statistics and hotspot analysis
are in [`PERFORMANCE.md`](PERFORMANCE.md). The release binds these results to
the exact physical runner, signing, notarization, and publication evidence.

The implementation is not considered compatible merely because it builds or passes unit tests. A stable release requires reproducible evidence from the pinned real-Docker oracle, stock Apple runtime, `container-compose`, and VS Code lanes described here. [`QUALITY.md`](QUALITY.md) defines the corresponding merge and release gates.

Direct oracle runs use the official `@devcontainers/cli` 0.88.0 package from
tag commit `f683c29f64a20109b4453e5149807e390ff65133`. Preflight verifies its pinned
npm SHA-512 integrity value before execution, and each lane fingerprint retains
that immutable package identity.

## Test objectives

The test system proves all of the following:

- Docker Engine requests used by the pinned Dev Containers toolchain have the expected status, headers, body, stream framing, errors, and lifecycle effects.
- Provider-neutral behavior is identical through the stock Apple and `container-compose` providers wherever the project claims support.
- A decoded unsupported Apple primitive fails before creating resources. The
  separate standards audit tracks Docker request members that 1.0.0 does not
  yet decode and reject.
- State reconciliation, cancellation, concurrent operations, and cleanup remain correct after partial failures.
- A pinned stable VS Code and Dev Containers extension can open, rebuild, reuse, and close a representative workspace without patches.
- Tests execute enough product code to meet a 90% overall line-coverage gate and a 90% changed-code line-coverage gate.
- Swift memory-safety and concurrency regressions are exercised with AddressSanitizer and ThreadSanitizer through a retrying, full-log harness.

## Test architecture

The suite is divided by the boundary it proves. A higher layer supplements rather than replaces the lower layers.

| Layer | Primary purpose | Environment | Merge or release role |
| --- | --- | --- | --- |
| Unit | Pure translation, parsing, state, and error behavior | Hosted macOS | Required on every relevant pull request |
| Docker wire contract | Exact HTTP and streaming contract over a Unix socket | Hosted macOS | Required when the API surface changes |
| Hosted integration | Real service process with fake runtime, Docker CLI, and deterministic faults | Hosted macOS | Required on every relevant pull request |
| Docker oracle integration | Official `@devcontainers/cli` against real Docker Engine | Hosted Linux or trusted live runner | Required for affected fixtures |
| Real runtime parity | Differential execution on stock Apple and `container-compose` | Dedicated physical Apple-silicon runner | Required for trusted main candidates and stable release |
| VS Code E2E | Real extension activation, attach, rebuild, and cleanup | Dedicated physical Apple-silicon runner | Required for stable release |
| Sanitizers | Memory and data-race detection | Hosted macOS, plus live runner where needed | ASan and TSan on pull requests, protected main, schedules, and stable candidates |

### Unit tests

Unit tests own fast, deterministic coverage of:

- Docker API version parsing and route negotiation;
- wire DTO encoding and decoding, including missing, extra, and malformed fields;
- native-to-Docker state and label projection;
- capability checks and typed unsupported-operation errors;
- provider selection, immutable project leases, and idempotency keys;
- stream multiplexing, TTY/non-TTY behavior, chunk boundaries, cancellation, and EOF;
- POSIX tar creation and extraction, modes, ownership, timestamps, symlinks, and long paths;
- event ordering, cursor resume, filtering, and reconnect behavior;
- SQLite migrations, crash recovery, reconciliation, and cleanup plans;
- deadlines, retry classification, privacy redaction, and diagnostic manifests.

Fakes use controllable clocks, deterministic identifiers, scripted byte
streams, and injected failures. Unit tests do not mock away the subject under
test: router tests exercise the real router, and migration tests use the real
schema against a temporary database.

### Docker wire contract tests

Contract tests start the actual HTTP server on a temporary user-owned Unix
socket and issue raw HTTP requests without the Docker CLI. They assert:

- the advertised API version and behavior of version-prefixed routes;
- status code, content type, required headers, and exact Docker error envelope;
- JSON field types, omission/null rules, label filters, and identifier consistency;
- upgrade/hijack behavior for attach and exec;
- eight-byte multiplexed stream headers and raw TTY streams;
- archive content and `X-Docker-Container-Path-Stat`;
- event-stream ordering, filtering, reconnect cursor, cancellation, and terminal behavior;
- success and error cases for every advertised endpoint and API version.

Every advertised endpoint has contract coverage for its success path and its
material errors. The service does not advertise an endpoint or API version that
lacks these tests.

### Hosted integration tests

Hosted integration tests run the compiled service as a separate process with
the real Unix socket, state store, logs, and cleanup paths, backed by a scripted
fake Apple executor. They exercise:

- Docker CLI black-box commands against a named temporary context;
- official `@devcontainers/cli` configuration discovery and request sequences where no nested runtime is required;
- process startup, readiness, graceful termination, forced termination, and restart;
- socket disconnects, truncated bodies, slow streams, deadlines, and child-process failures;
- resource reconciliation after the fake executor reports partial native state;
- multiple clients acting on the same and different projects.

The fake runtime records the provider-neutral requests it receives. Tests
compare those requests with expected capability, idempotency, deadline, and
project-lease values rather than only checking the final CLI exit code.

## Differential parity harness

### Required logical backends

The parity harness executes a common fixture model against three logical backends:

1. **Docker oracle:** a pinned real Docker Engine and Docker Compose installation driven by the pinned official `@devcontainers/cli`.
2. **Stock Apple:** the product under test backed only by official `apple/container` and `apple/containerization`.
3. **`container-compose` provider:** the same compatibility service, with
   Stephen Clarke's separately installed `container-compose` executable and
   matched runtime selected for Compose planning and lifecycle. Apple does not
   supply this provider.

The Apple providers are not interchangeable provenance labels. The stock lane
contains only official Apple runtime dependencies. Because the supported
`container-compose` release requires Stephen's matched runtime stack, the
provider lane records `container-compose/matched-fork` and does not describe that
runtime as stock Apple.

### Release lanes

Each stable candidate retains three provider recordings:

- Docker oracle against the candidate fixture and exact oracle pins;
- stock Apple against the pinned supported Apple release;
- `container-compose` against its pinned release and matched Apple stack.

Every recording identifies the exact project commit, macOS and Xcode build,
Docker Engine, Docker Compose, `@devcontainers/cli`, Apple runtime,
`container-compose`, fixture revision, image digest, and VS Code versions that
produced it. Separate forward-compatibility runs against moving upstream
branches never replace the stable matrix.

### Fixture execution and assertions

Common fixtures run through all three logical backends. A Compose-specific feature that stock Apple cannot represent may compare Docker with `container-compose`, but the stock lane must then pass a checked-in negative contract that proves a typed, side-effect-free `unsupportedCapability` result. A regression cannot be reclassified as unsupported to make a run pass.

Each fixture follows the same state machine:

1. Validate exact tool and source pins and create an isolated workspace.
2. Assert that no fixture-owned resources exist.
3. Run the operation through the selected unmodified consumer.
4. Capture raw protocol, process, runtime, filesystem, network, and lifecycle evidence.
5. Produce a normalized semantic observation.
6. Assert the fixture's direct invariants.
7. Compare the Apple observation with the Docker oracle observation.
8. Tear down and prove that no fixture-owned processes, sockets, containers, images, networks, volumes, mounts, or state rows remain.

An exit code of zero is necessary but not sufficient. The harness asserts
observable files, ownership and modes, environment, user identity, process exit
state, port connectivity, labels, resource topology, lifecycle ordering, error
class, and cleanup.

### Normalization boundary

Normalization removes nondeterminism only:

- runtime identifiers mapped consistently within a recording;
- fixture-specific temporary workspace roots;
- timestamps and durations, while retaining presence, order, timeout class, and deadline assertions;
- dynamically assigned ports and IP addresses mapped to stable tokens, while still testing connectivity and routing;
- ANSI progress rendering;
- fields explicitly documented by the source contract as unordered.

Normalization must never remove or rewrite:

- exit codes, stderr, warnings, Docker error envelopes, or typed product errors;
- missing resources, lifecycle transitions, event ordering, or cleanup failures;
- file content, ownership, mode, symlink target, mount behavior, or user identity;
- connectivity failures, protocol framing, retry count, timeout class, or cancellation outcome;
- a stock-runtime limitation that changes requested semantics.

Both raw and normalized evidence are retained. A stable release requires zero
unexplained semantic differences in the claimed fixture scope. There is no
expected-failure list, parity waiver, ignored fixture, or allow-failure release
lane.

### Timing evidence

Every fixture records monotonic wall-clock `durationSeconds` in its lane JSON and JUnit testcase. The comparison JSON and Markdown matrix preserve the three raw durations and compute stock-Apple/Docker and `container-compose`/Docker ratios only between matching fixtures.

Timing is not an exact-equivalence assertion. A completed candidate remains passing when it is slower than Docker by less than one order of magnitude. A timeout or other non-completion, missing or invalid timing evidence, or a candidate duration of at least `10x` the Docker oracle for the same fixture fails the parity gate. The harness does not retry, normalize, or waive those failures. The complete 1.0.0 repeated-run analysis and optimization measurement protocol are in [`PERFORMANCE.md`](PERFORMANCE.md).

## Fixture catalog

The machine-readable manifest is the source of release scope. Fixture identifiers remain stable, and every entry records required lanes, pins, capabilities, observations, cleanup assertions, implementation status, and owner.

### Engine API fixtures

- Ping, version negotiation, supported API-prefix handling, `HEAD /_ping`,
  Docker error envelopes, and malformed create requests.
- Container create, inspect, start, wait, exit status, remove, and repeated-remove
  behavior.
- Exec TTY and non-TTY output, user/environment/workdir selection, byte-exact
  4 MiB stdin/stdout transfer, stderr separation, and exact exit status.
- Dockerfile build arguments and labels, plain progress output, and a failed
  build stream.
- Archive copy in and out with content, file mode, symlink, long-path, and
  1 MiB file preservation.
- Network and volume creation and inspection, service-name DNS, read-only bind,
  tmpfs, and named-volume persistence.

### Dev Container fixtures

- Image-based configuration, environment, workspace, lifecycle hook, and reuse.
- Dockerfile configuration, build arguments, target stages, and build context.
- Container and remote users, explicit `updateRemoteUserUID: false`, container/remote environment, and container-environment expansion.
- String-valued lifecycle command order across initialize, create, update, post-create, start, and attach.
- Two public Dev Container Features, generated build context, lockfile use, and frozen-lock rejection.
- Workspace mounts, bind mounts, named volumes, port attributes, TCP publishing, and forwarding.
- Same-configuration reuse, forced replacement, lifecycle-hook counts, and
  explicit container/volume cleanup.

Release evidence records the resolved runtime and client fingerprints.
Several 1.0.0 fixtures still name public image or Feature tags, so upstream
content can drift between rebuilds. Converting every fixture input to an
immutable digest is a reproducibility follow-up; a tag alone is not treated as
proof of an immutable test input.

### Compose fixtures

- Selected primary service, generated override, and workspace projection.
- `runServices`, dependency health, and service-name DNS.
- Environment files, a named volume, a named network, and network aliases.
- Restart, recreate, stop-signal handling, primary-container discovery,
  project down, and cleanup.

### Fault and concurrency fixtures

- A missing backend socket returns an error.
- Four concurrent starts of one created container complete successfully.
- Four concurrent forced removes converge on an absent container.
- A `TERM` signal produces the expected container exit status.
- An empty, filtered event stream returns within its bounded interval.

The concurrency probe asserts the observed final container state and fixture
cleanup. Wider fault injection and deterministic scheduler coverage remain
future work and are not part of the 1.0.0 parity claim.

## Real runtime matrix

### Runner requirements

GitHub-hosted `macos-26` is suitable for Swift builds, unit/contract tests, coverage, lint, and sanitizer jobs, but its hosted virtual machine cannot provide the nested Apple container runtime needed for live parity. Real Apple and VS Code tests require a dedicated physical Apple-silicon Mac with:

- macOS 26 and the selected Xcode 26 toolchain;
- recommended minimum 32 GB memory and 100 GB free disk for concurrent runtime images and artifacts;
- real Docker Engine access for the oracle;
- stock Apple `container`, the separately installed `container-compose`, and pinned VS Code installations;
- repository and runtime source checkouts outside Desktop and Documents to avoid Apple `vmnet` path-related failures;
- labels such as `self-hosted`, `macOS`, `ARM64`, `macos-26`, and `devcontainer-live`.

Live jobs use three provenance-specific self-hosted runner labels:
`devcontainer-docker`, `devcontainer-apple-stock`, and
`devcontainer-container-compose`. One isolated Mac may carry all three labels only
when the workflow serializes them and validates the exact selected runtime
before each lane. Each run creates an explicit application root, Docker
context, socket, state database, runtime namespace, and fixture prefix. Cleanup
runs even after cancellation and fails the job if owned resources remain.

The self-hosted runner never executes untrusted public-fork pull-request code.
Live runs are limited to an exact trusted commit from protected `main`, a
schedule, or an explicit maintainer dispatch after hosted checks pass. Release
credentials are unavailable to test steps.

### Matrix policy

| Candidate | Docker oracle | Stock Apple | `container-compose` provider | VS Code |
| --- | --- | --- | --- | --- |
| Pull request | Hosted affected fixtures where safe | Not on self-hosted runner | Not on self-hosted runner | No |
| Protected main | Full | Full | Full | Full |
| Nightly | Full | Full | Full | Full |
| Stable candidate | Full | Full | Full | Full |

Changes to fixture definitions, the normalizer, comparison rules, or release manifest force the full matrix. A path filter may skip only a lane that cannot be affected, and the required-check aggregator must report the reason and still succeed or fail explicitly.

## VS Code end-to-end tests

The E2E suite pins VS Code 1.130.0 for arm64 at commit
`1b6a188127eeaf9194f945eb6eb89a657e93c54c`, Dev Containers extension 0.467.0,
and its embedded Dev Container CLI 0.88.0 at
`f683c29f64a20109b4453e5149807e390ff65133`. The driver authenticates the
official application, VSIX, and embedded CLI by checked-in SHA-256 digests.
The installed CLI is required because its command set includes the `open`
operation used by VS Code; a standalone `@devcontainers/cli` package must not
be assumed to expose identical commands.

A small test-only workspace probe extension records activation, remote
authority, remote operating system, workspace path, forwarded ports, command
results, and container identity. The fixture:

1. Open a folder in a Dev Container through the compatibility socket.
2. Wait for the remote extension host and probe activation.
3. Verify workspace content, remote user, environment, settings, and a command in the integrated remote context.
4. Verify port forwarding with an actual request.
5. Close and reopen without rebuilding, asserting resource reuse.
6. Rebuild after a configuration change, asserting replacement and cleanup.
7. Close the workspace and prove no fixture-owned resources remain.

The Docker recording has passed locally against the exact pins above. The same
scenario is dispatched independently through stock Apple and
`container-compose`, then all observations are compared with that Docker
recording. VS Code Insiders may run as an early warning, but it cannot replace
the pinned stable-build release gate.

Screenshots are diagnostic artifacts only. Assertions come from machine-readable extension output, process state, protocol recordings, and runtime observations.

The host process environment is fail-closed. Runtime commands receive an
explicit non-secret allowlist, while VS Code receives a narrower allowlist,
an isolated home and temporary directory, disabled login-shell environment
import, and in-memory secret storage. Before publication, the harness scans
all JSON and log evidence for credential-shaped environment names. A match
deletes the affected evidence file and fails the lane; it can never become an
uploaded artifact or a passing result.

## Coverage design

### Required thresholds

The merge and stable-release gates are:

- **90% line coverage overall** across first-party executable Swift source included in the coverage report.
- **90% line coverage on changed executable lines** relative to the pull request merge base or, for a direct push, the appropriate protected-branch comparison commit.

Both thresholds are hard gates. Rounding occurs only for display; a measured value below 90.00% fails. Generated source, third-party dependencies, test fixtures, and vendored code may be excluded only through a reviewed, path-specific configuration. Production adapters, errors, and cleanup code are not excluded merely because they are difficult to execute.

Unit tests are expected to contribute most branch, translation, and error-path coverage because they are fast and deterministic. Contract and hosted integration tests must run an instrumented product process so their executed first-party lines contribute to the same report. Real parity and VS Code E2E may contribute only when the candidate binary is instrumented and emits valid profiles; those lanes remain mandatory release gates regardless of whether their coverage is merged.

### Collection and merge

The implemented Swift coverage flow is:

1. Build tests and product executables with `swift test --enable-code-coverage`.
2. Run unit, contract, and hosted integration suites with unique `LLVM_PROFILE_FILE` patterns.
3. Require a normal test-process exit and non-empty raw profiles.
4. Merge all valid profiles with `llvm-profdata merge -sparse`.
5. Export source coverage with `llvm-cov`, including each instrumented first-party binary.
6. Produce LCOV for the overall and changed-line check and SonarQube generic coverage XML for SonarCloud.
7. Map the merge-base diff to executable Swift lines in the LCOV report and fail when overall or changed-line coverage is below 90%.

The coverage checker fails closed on a missing test binary, missing profile,
unrecognized source path, empty test execution, or source file absent from the
report. It prints numerator, denominator, exclusions, merge base, and uncovered
changed lines.

The runner harness uses two coverage attempts and
`SWIFT_TEST_ACCEPT_SIGNAL_13=0`. Accepting a
`swiftpm-testing-helper` signal 13 after apparently passing output can leave
incomplete profiles and report false 0% coverage, so it is never accepted
during coverage collection.

Coverage is evidence of exercised lines, not behavioral parity. Tests are not
weakened, merged, or deleted solely to improve the percentage, and a 90%
result cannot override a failed contract, sanitizer, parity, or E2E gate.

## Memory and concurrency tooling

The sanitizer design deliberately follows the current `container-compose` CI mechanism so both projects diagnose SwiftPM failures consistently.

The shared harness is implemented as `Tools/ci/run-swift-test.sh`, matching the
`container-compose` path and interface. It:

- write complete output to `SWIFT_TEST_RESULT_LOG`, defaulting to `.build/swift-test.log`;
- run `SWIFT_TEST_ATTEMPTS`, defaulting to two attempts;
- print the last `SWIFT_TEST_TAIL_LINES`, defaulting to 200, after success;
- retry when the log contains `swiftpm-testing-helper` with `signal code 13`;
- distinguish explicit passing output from Swift Testing/XCTest failure output;
- control the post-pass signal-13 fallback with `SWIFT_TEST_ACCEPT_SIGNAL_13`;
- optionally bound a run with `SWIFT_TEST_TIMEOUT_SECONDS`, terminate the
  command's entire process group, retain its diagnostic output, and retry.

The timeout is disabled by default. Hosted CI coverage, Sonar coverage, and
sanitizer jobs set it to 1,200 seconds so a wedged `swiftpm-testing` process
cannot consume the full hosted-job timeout. An ordinary test failure is never
retried as a timeout and continues to fail the gate immediately.

The AddressSanitizer job uses the same command shape as `container-compose`:

```console
SWIFT_TEST_RESULT_LOG=.build/swift-asan.log \
  SWIFT_TEST_ATTEMPTS=2 \
  Tools/ci/run-swift-test.sh swift test --disable-automatic-resolution --sanitize=address --no-parallel
```

The ThreadSanitizer job uses:

```console
SWIFT_TEST_RESULT_LOG=.build/swift-tsan.log \
  SWIFT_TEST_ATTEMPTS=2 \
  Tools/ci/run-swift-test.sh swift test --disable-automatic-resolution --sanitize=thread --no-parallel
```

This project reuses the Compose stack's retry and full-log implementation but
sets `SWIFT_TEST_ACCEPT_SIGNAL_13=0` for ASan, TSan, and coverage. A toolchain
signal therefore cannot convert an incomplete execution into release evidence.

ASan and TSan run on relevant pull requests, `main`, schedules, explicit
dispatches, and the exact stable candidate. Both run with `--no-parallel`,
separate SwiftPM build/cache directories, full uploaded logs, and no filtering
that omits production modules.

Any AddressSanitizer finding, ThreadSanitizer warning, unexpected test failure, missing test execution, or accepted signal-13 fallback fails the stable gate.

## Evidence and reproducibility

Every non-unit run publishes a manifest containing:

- candidate and workflow commit SHAs;
- complete toolchain and runtime fingerprints;
- fixture and normalization schema versions;
- exact commands and sanitized environment;
- raw logs, protocol captures, observations, normalized comparison, and cleanup result;
- test counts, retries, durations, sanitizer mode, and coverage profile inventory;
- cryptographic digests for every retained file.

Secrets, tokens, host usernames, and unrelated paths are redacted before upload. Redaction is structural and tested; it must not alter the fields used for parity. Stable evidence is retained with the release record, while pull-request artifacts may use a shorter retention period.

## Release evidence

The service, unit/contract/integration suite, differential CLI harness,
sanitizer jobs, coverage gate, and pinned VS Code fixture are implemented.
Stable publication is fail-closed unless the exact release commit has:

1. clean, isolated stock-Apple and `container-compose`-provider CLI recordings;
2. clean, isolated stock-Apple and `container-compose`-provider VS Code recordings;
3. a successful three-lane semantic comparison and cleanup proof;
4. candidate-bound signing, notarization, package verification, and quality
   evidence.

Documentation must not promote a locally implemented fixture to a
compatibility claim until its required candidate-bound recordings exist.

## Primary references

- [Docker Engine API](https://docs.docker.com/reference/api/engine/)
- [Dev Container CLI](https://github.com/devcontainers/cli)
- [VS Code Dev Container CLI](https://code.visualstudio.com/docs/devcontainers/devcontainer-cli)
- [VS Code extension testing](https://code.visualstudio.com/api/working-with-extensions/testing-extension)
- [Apple container](https://github.com/apple/container)
- [Building Apple container](https://github.com/apple/container/blob/main/BUILDING.md)
- [Swift Package Manager test and coverage options](https://docs.swift.org/swiftpm/documentation/packagemanagerdocs/swifttest/)
- [GitHub-hosted runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [GitHub self-hosted runner security](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
