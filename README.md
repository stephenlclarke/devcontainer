# devcontainer

<!-- markdownlint-disable MD013 MD033 -->
<p>
  <img align="left" hspace="20" src="docs/images/devcontainer-icon.png" width="147" alt="devcontainer icon: a blue glass cube overlapping the standard three-row container service panel" />
  <a href="https://github.com/stephenlclarke/devcontainer/actions/workflows/ci.yml?query=branch%3Amain"><img alt="CI" src="https://github.com/stephenlclarke/devcontainer/actions/workflows/ci.yml/badge.svg?branch=main" /></a>
  <a href="https://github.com/stephenlclarke/devcontainer/actions/workflows/codeql.yml?query=branch%3Amain"><img alt="CodeQL" src="https://github.com/stephenlclarke/devcontainer/actions/workflows/codeql.yml/badge.svg?branch=main" /></a>
  <a href="https://github.com/stephenlclarke/devcontainer/actions/workflows/docs.yml?query=branch%3Amain"><img alt="Documentation" src="https://github.com/stephenlclarke/devcontainer/actions/workflows/docs.yml/badge.svg?branch=main" /></a>
  <a href="https://github.com/stephenlclarke/devcontainer/actions/workflows/homebrew.yml?query=branch%3Amain"><img alt="Homebrew" src="https://github.com/stephenlclarke/devcontainer/actions/workflows/homebrew.yml/badge.svg?branch=main" /></a>
  <a href="https://github.com/stephenlclarke/devcontainer/actions/workflows/prebuilt-binaries.yml?query=branch%3Amain"><img alt="Releases" src="https://github.com/stephenlclarke/devcontainer/actions/workflows/prebuilt-binaries.yml/badge.svg?branch=main" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Quality Gate Status" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&amp;metric=alert_status" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Coverage" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&amp;metric=coverage" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Bugs" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&amp;metric=bugs" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Code Smells" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&amp;metric=code_smells" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Security Rating" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&amp;metric=security_rating" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Maintainability Rating" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&amp;metric=sqale_rating" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Duplicated Lines" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&amp;metric=duplicated_lines_density" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Lines of Code" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&amp;metric=ncloc" /></a>
  <img alt="Repo Visitors" src="https://visitor-badge.laobi.icu/badge?page_id=stephenlclarke.devcontainer" />
</p>
<br clear="left" />
<br>
<!-- markdownlint-enable MD033 -->

Run VS Code-compatible Development Containers on Apple silicon through stock [`apple/container`](https://github.com/apple/container), with first-class support for [`container-compose`](https://github.com/stephenlclarke/container-compose).

The project's north-star goal is 100% behavioural parity with Docker-based Development Containers, with comparable or better user-visible performance. Current releases make narrower evidence-bound claims until the complete specification and performance objectives are proved. The audited findings and solution designs are in the [full parity and performance roadmap](PARITY-ROADMAP.md).

> [!IMPORTANT]
> Version 1.0.1 is the latest immutable stable baseline. Its exact tag
> certification ran all 18 CLI fixtures plus the real VS Code end-to-end
> fixture against real Docker, unmodified Apple `container` 1.1.0, and the
> separately maintained `container-compose` 0.10.1 provider stack with zero
> normalized semantic differences. [COMPATIBILITY.md](COMPATIBILITY.md)
> records those exact release fingerprints without rewriting the historical
> evidence as the source dependency graph changes.

The latest source-bearing `main` revision (`1b71fe3ec105`) passes hosted CI,
the stock Apple compile/test lane, documentation, Homebrew validation,
AddressSanitizer, ThreadSanitizer, CodeQL, and SonarQube. Its 15 September 2026
SonarQube analysis reports 95.5% coverage, 0.1% duplicated lines, and zero
bugs, vulnerabilities, code smells, or security hotspots. The live runtime
workflow did not produce the expected lane result files, so this source is not
yet a replacement for the immutable 1.0.1 runtime-parity baseline. The
published Current package also still points to July source
`b31e80b2b9c09`; do not infer a current-source release from the green hosted
quality badges.

Current source has distinct dependency profiles. The stock profile resolves
unmodified Apple `container` 1.4.1 and `containerization` 0.45.0; the enhanced
profile resolves the exact Stephen-owned revisions recorded in
[COMPATIBILITY.md](COMPATIBILITY.md). Neither profile has replaced the 1.0.1
runtime-parity baseline because the latest source-bearing runtime workflow did
not produce complete lane evidence.

## See it work

![Live terminal recording of a Dev Container starting and running on stock Apple container](docs/images/devcontainer-demo.gif)

The recording starts the local compatibility endpoint, runs the official
`@devcontainers/cli` against the checked-in [hello example](Examples/hello),
executes its lifecycle hook, reads the mounted workspace from the running
Apple container, and proves exact cleanup. It is generated from
[docs/devcontainer-demo.tape](docs/devcontainer-demo.tape) with
[VHS](https://github.com/charmbracelet/vhs); every displayed result comes from
the live command immediately above it. Recreate it on a release host with
`make demo`.

## Design promise

The project keeps the official [Dev Containers](https://github.com/devcontainers)
toolchain above a local Docker Engine compatibility service. VS Code and the
reference [`@devcontainers/cli`](https://github.com/devcontainers/cli) remain
unmodified; the service translates their tested Docker API subset into
Apple-native runtime operations.

```mermaid
flowchart LR
    VS["VS Code Dev Containers"] --> DC["Official @devcontainers/cli"]
    DC --> Docker["Unmodified Docker CLI"]
    Docker --> API["Local Docker Engine compatibility socket"]
    API --> Shared["container-engine generated API 1.44 through 1.53 gateway"]
    Shared --> Session["Private fingerprint-bound provider session"]
    Session --> Core["devcontainer stock adapter and provider-neutral runtime core"]
    Core --> Stock["Stock apple/container"]
    DC --> ComposeChoice{"Compose provider"}
    ComposeChoice --> DockerCompose["Docker Compose over the bridge"]
    ComposeChoice --> ContainerCompose["container-compose adapter"]
    DockerCompose --> API
    ContainerCompose --> Stock
```

The `container-compose` integration is first-class but independently installed.
The core does not import `ComposeCore`, and installing this project never
silently replaces stock Apple `container` with the matched fork stack.

The stock adapter can export a stopped container's canonical name, stable
Docker identifier, immutable Apple bundle key, and lifecycle snapshot for a
coordinated provider handoff. The export fails closed while a selected
container is running or any create, start, stop, restart, kill, rename, remove,
exec, or exit transition can race the snapshot; it never invents event history
that the legacy poller cannot prove.

## Compatibility target

| Lane | Purpose | Stable-release requirement |
| --- | --- | --- |
| Real Docker | Behavioral oracle using pinned Docker Engine, Docker Compose, and `@devcontainers/cli` | Complete raw and normalized evidence |
| Stock Apple | Official `apple/container` only; Docker Compose uses the compatibility API | Zero semantic differences in every claimed fixture |
| `container-compose` provider | Stephen Clarke's separately installed `container-compose`, with its exact runtime provenance recorded | Zero semantic differences in every claimed fixture |

The test plan covers image, Dockerfile, Features, users, environment, lifecycle hooks, workspace mounts, ports, reuse, Compose services, networks, volumes, failure recovery, and real VS Code attach/rebuild behavior. See [TESTING.md](TESTING.md), [COMPATIBILITY.md](COMPATIBILITY.md), and the explicit [standards conformance audit](CONFORMANCE.md).

Stock `apple/container` 1.1.0 does not expose create-time hostname, full Docker
privileged mode, or most Docker security-option fields. Requests for those
semantics fail before runtime creation; privileged mode is never approximated
with `CapAdd ALL`. Runtime-affecting container, exec, network, and volume
request objects use strict nested decoding. Unknown fields return a Docker
`400`, and known fields that the selected runtime cannot enforce return `501`,
before side effects. Arbitrary `runArgs` remain outside the compatibility
claim until their exact behaviour is certified. See
[CONFORMANCE.md](CONFORMANCE.md) before using security, device, resource, or
advanced mount options.

## Project layout

| Path | Purpose |
| --- | --- |
| [USER_GUIDE.md](USER_GUIDE.md) | Installation-to-operation user manual for the stock and optional provider paths |
| [DESIGN.md](DESIGN.md) | Implemented architecture, data flow, runtime boundaries, security, and release definition |
| [Docker-free review and design](docs/docker-free-review-and-design.md) | September 2026 audit, current defects, stock/enhanced architecture, Compose reuse, and optimisation/test plan; proposed work |
| [PARITY-ROADMAP.md](PARITY-ROADMAP.md) | North-star parity and performance criteria, audited defects, and designed solutions |
| [UNSUPPORTED-CAPABILITIES.md](UNSUPPORTED-CAPABILITIES.md) | Field-by-field implementation and certification design for every current unsupported capability |
| [CONFORMANCE.md](CONFORMANCE.md) | Complete audited Dev Containers property ledger and explicit 1.0.1 non-conformances |
| [PERFORMANCE.md](PERFORMANCE.md) | Full repeated-run parity timing analysis and optimization priorities |
| [TESTING.md](TESTING.md) | Docker, stock Apple, and separate `container-compose` differential harness |
| [QUALITY.md](QUALITY.md) | Software-quality analysis, measurable gates, and supply-chain controls |
| [BUILD.md](BUILD.md) | Current local build, test, coverage, sanitizer, parity, and package commands |
| [INSTALL.md](INSTALL.md) | Source, prebuilt, Homebrew, provider, and uninstall contract |
| [RELEASE.md](RELEASE.md) | CI/CD, GitHub Pages, release authority, and Homebrew tap design |
| [COMPATIBILITY.md](COMPATIBILITY.md) | Compatibility contract and explicit claim policy |
| [SECURITY.md](SECURITY.md) | Private vulnerability reporting and supported-version policy |
| [Tests/Parity](Tests/Parity) | Machine-readable parity manifest and executable differential fixtures |
| [Examples/hello](Examples/hello) | Minimal image-based Dev Container used by the live demonstration |
| `container-engine-api` revision `84830606abf9` | Shared executable and libraries pinned by both source profiles, including the generated 107-operation Docker API 1.44 through 1.53 ledger, wire/router/server contracts, schema-2 private provider-session transport, bounded raw/WebSocket streaming, deterministic listener ownership/shutdown, and provider-owned immutable state-root identity. This revision is newer than the published 0.3.5 tag and is not stable-release evidence. |
| `Sources/DevContainerDockerAPI` | Stock-provider Docker Engine endpoint policy and DTO projection |
| `Sources/DevContainerAppleRuntime` | Stock Apple runtime adapter and process/port/archive support |
| `Sources/DevContainerService` | Stock-provider adapter; normal mode starts one internal private provider session behind the shared public gateway, while `--provider-socket` exposes only the private session for an external `container-engine` process |
| `Sources/DevContainerComposeProvider` | Optional external `container-compose` dispatcher |

## Development

The opt-in [native Bazel build](docs/bazel-workflow.md) builds all three executables and runs the unit suites against either stock Apple or enhanced dependencies. Use `make bazel-configure` once, then `make bazel-build` and `make bazel-unit`; add `BAZEL_PROFILE=stock` for the stock graph. `make bazel-package` creates an unsigned native candidate. Scratch and caches stay on the enrolled external SSD; test evidence and candidate archives are retained on internal storage, with authenticated restore that requires no rebuild. Runtime parity, signing and release publication have not yet moved to this workflow.

Use `make bazel-coverage-report INVOCATION=ID` to export a retained unit run's LCOV and Sonar XML without rerunning tests. `Tools/bazel/run.sh coverage-report ID --minimum-percent 90` also checks the raw measured percentage and fails below the target, while leaving the report available for diagnosis. The receipt identifies the tested commit/profile; exporting historical coverage does not make it current-head quality evidence. The shared checker also supports Compose's reviewed profile-specific test and production-source inventories.

Consumer coverage keeps unit-only evidence distinct from an explicitly declared `unit-cli` inventory. Compose's combined inventory includes all unit targets plus its native no-runtime CLI contracts; use `--inventory=unit-cli` at its quality gate. The receipt and gate bind that choice as well as source/profile/policy. Devcontainer's current native aggregate remains unit-only; neither scope establishes live integration or parity.

New Bazel build/test invocations also retain elapsed timings, platform/toolchain identity and cache metrics. Use `make bazel-build-timings INVOCATION=ID BASELINE=ID` to compare matching configurations. Ordinary timings are labelled observations; controlled quiet-machine benchmarks remain a separate performance gate.

The [test-harness replacement](docs/bazel-test-harness.md) is in progress. `make bazel-harness` runs its deterministic recovery and real Unix-socket component tests, including Engine negotiation, lifecycle, exec-stream, archive-copy and network/volume assertions, and journalled test-resource ownership, under Bazel without building products or starting container services. These checks do not yet establish live runtime parity; all 19 existing fixtures remain required for cutover.

The new harness has a [recorded three-lane E01 functional comparison](docs/evidence/released-common-e01-20260918b.md), with [exact fingerprints and raw timings](docs/evidence/released-common-e01-20260918b.json). It passes that protocol fixture only, not the complete matrix or performance gate. Raw Apple-lane operation ratios exceed 10x and require quiet paired investigation. `make bazel-parity-report CAMPAIGN=<id>` reads sealed results without rerunning workloads; missing fixtures fail instead of silently narrowing scope. Select one fixture explicitly with `CASE_FIXTURE=<id>` and choose JSON, Markdown or JUnit with `REPORT_FORMAT=json|markdown|junit`. Private logs and unexpected payloads are never exported.

The [final delivery contract](docs/bazel-workflow.md#final-delivery-and-public-evidence) includes stable releases of both projects, a full published-binary test cycle, public quiet-host benchmark evidence, both DocC sites, and installation-first live demos. Stock E01 passes against published binaries; E02 lifecycle, E03 exec/streams, E05 archive copy and E06 network/volume fixtures now have passing retained-candidate results against stock Apple. E05's archive-permission difference under a restrictive host file mask was fixed and passed its unchanged live assertions. These results do not replace the certified historical release matrix, close the transport coverage shortfall or establish new full parity.

The development native-create path now uses a durable creation journal (state schema 4). Failed or uncertain creates remain inspectable but cannot be started, restarted, executed, renamed or used for archive transfers through the bridge until reconciled. Ordinary deletion does not erase unresolved intent. An operator reconciliation interface remains unfinished; do not treat this draft recovery path as release-ready. Upgrading a disposable development state root is supported from schema 2/3; older binaries cannot reopen schema 4. Back up a quiescent production state root before migration and retain it for rollback. See [creation recovery](DESIGN.md#native-creation-recovery).

`make bazel-prepare-guest-images` prepares the digest-pinned stock initialization and shared workload images without Docker or a VM, reusing Compose's OCI validator. Download staging stays on the enrolled SSD; verified archives remain on internal storage. `OFFLINE=1` reuses retained inputs without network access. This preparation requires Homebrew Skopeo. `make bazel-prepare-guest-kernel` separately prepares the checksum-pinned kernel recommended by stock Container 1.4.1, using Homebrew Zstandard for extraction; `OFFLINE=1` also supports verified reuse. Neither command installs or boots a guest. Enhanced initialization-image acquisition and complete guest-runtime qualification remain outstanding.

`make bazel-prepare-releases` verifies the pinned GitHub releases, extracts them on the enrolled SSD, and retains their verified executable trees on internal storage as long-lived assets. It does not install packages or rebuild products. Add `OFFLINE=1` to use verified retained downloads. Repeated preparation reuses complete durable trees without re-extraction; interrupted publication resumes only its registered files, and changed sealed files fail closed. Disposable state/build/test work remains on SSD. This prepares binaries only, not the complete guest-image, Docker-oracle or VS Code runtime environment.

`make bazel-prepare-docker-oracle` prepares the pinned published Colima/Lima tools and compressed Docker VM image through that same path; `OFFLINE=1` supports verified reuse. `make bazel-prepare-docker-cli` separately downloads the existing Docker 29.6.2 oracle's exact Homebrew bottle from public GitHub Packages using Skopeo, checks its executable checksum against the parity manifest, and retains the client plus licence/notices without installing Homebrew packages. `OFFLINE=1` verifies the retained client without downloading, extracting or repairing it. Neither preparation command starts a VM, changes Docker contexts or touches existing Colima profiles. The opt-in `make bazel-engine-case CAMPAIGN=<id> LANE=docker` adapter uses a private SSD VM for E01, E02, E03, E05 and E06; live qualification and interrupted-VM recovery remain unfinished. It does not connect to an installed Docker daemon. Docker is a test reference only, not a product installation dependency.

`make bazel-engine-case CAMPAIGN=<explicit-id> LANE=apple-stock` runs the released Engine negotiation case; use `LANE=container-compose` for the enhanced runtime binary. It requires already prepared releases, serializes access with Compose, and retains case evidence internally. On an idle host it temporarily suspends the explicitly scoped family services/CI listeners, starts the selected released API with SSD-only state, and restores the original services afterward. This opt-in metadata/protocol test does not launch guest workloads or establish full parity. See the harness document for quarantine and recovery limitations before using legacy runtime workflows alongside it.

For development-only integration, first run `make bazel-prepare-candidate CANDIDATE_INVOCATION=<retained-build-id>`, then add that same `CANDIDATE_INVOCATION` to `make bazel-engine-case`. This restores and authenticates a clean native candidate without rebuilding; the provider and guest inputs still come from published assets. Results are explicitly marked `local-candidate-integration-only`, cannot mix with a published-release comparison, and do not satisfy final release parity. Retained candidate admission works after disposable SSD build outputs have been removed.

The runtime harness owns a disposable keychain inside each isolated SSD test HOME; it does not reset or change your login keychain. Creation/deletion helpers are journalled, bounded and noninteractive. Uncertain helper completion retains quarantine instead of deleting its files. See [test-keychain recovery](docs/bazel-test-harness.md#isolated-test-keychain) if a run stops during credential initialization.

Adding `CASE_FIXTURE=E02-container-lifecycle`, `CASE_FIXTURE=E03-exec-streams`, `CASE_FIXTURE=E05-archive-copy` or `CASE_FIXTURE=E06-network-volume` selects the new guest-backed adapter. It requires read-only admission of the retained kernel, exact provider initialization image and workload image before changing services. Setup loads those local archives only into the disposable provider store; it does not build or pull images. Guest and network/volume cleanup precedes Engine/provider shutdown. This path is implemented and component-tested but not yet live-qualified; enhanced admission currently refuses its missing initialization input, and uncertain guest/process recovery still requires explicit reconciliation. Do not treat these targets as release qualification or bypass an outstanding host permission/quarantine.

After an interrupted service transaction, `make bazel-recover-runtime` reports whether its journal can be reconciled without changing services. If it reports `ready-to-restore`, `ready-to-retain-and-restore` or `ready-to-clear`, use `make bazel-recover-runtime-apply CASE_ID=<reported-case-id>` to retain any missing verified-stopped setup diagnostics, restore the recorded services and clean only the owned scratch directory. Recovery preserves the original failed case and refuses live or uncertain client processes; it is not permission to bypass quarantine or rerun a failed case.

Requirements are Xcode 26, Swift 6.2 or newer, Python 3, Ruby 2.7 or newer
(including its standard JSON and Psych YAML libraries), and `make`.
Runtime parity additionally requires a physical Apple-silicon Mac on macOS 26,
stock Apple `container`, real Docker, the pinned Dev Container CLI, and the
selected Compose provider.

The stock multi-service path uses the upstream `docker-compose` client over
this project's compatibility socket. The separately selected
`container-compose` provider remains optional and independently installed.

```console
make check
make test
make docs
make serve-docs
DEVCONTAINER_VSCODE_LIVE=1 make parity-vscode-docker
```

Use `devcontainer diagnostics --output devcontainer-diagnostics.tar.gz` to
create a bounded, privacy-redacted support archive whose JSON manifest is
printed before the archive is written.

Live runtime tests are deliberately not run on public pull-request code or GitHub-hosted macOS. They execute on an isolated physical runner only after a trusted exact commit has passed hosted checks.

## Documentation

Start with the [user guide](USER_GUIDE.md), then consult the
[compatibility contract](COMPATIBILITY.md), [standards conformance
audit](CONFORMANCE.md), [full parity and performance roadmap](PARITY-ROADMAP.md),
and [parity timing analysis](PERFORMANCE.md). The
generated [DocC site](https://stephenlclarke.github.io/api/devcontainer/) in the
[Container developer API collection](https://stephenlclarke.github.io/api/)
contains the public Swift API reference plus architecture, use, compatibility,
conformance, testing, and performance articles. GitHub Pages publishes it from
the exact `main` commit that passes the documentation workflow.

## Primary upstream references

The design follows the maintained sources in the [Dev Containers GitHub organization](https://github.com/devcontainers):

- [Development Containers Specification](https://github.com/devcontainers/spec)
- [Dev Container CLI reference implementation](https://github.com/devcontainers/cli)
- [Dev Container Features](https://github.com/devcontainers/features)
- [Dev Container Templates](https://github.com/devcontainers/templates)
- [Dev Container Images](https://github.com/devcontainers/images)
- [Dev Container CI](https://github.com/devcontainers/ci)
- [containers.dev specification and supporting tools](https://containers.dev/)

Runtime references are [Apple container](https://github.com/apple/container), [Apple containerization](https://github.com/apple/containerization), and the [Apple container API documentation](https://apple.github.io/container/documentation/). VS Code behavior is documented in [Developing inside a Container](https://code.visualstudio.com/docs/devcontainers/containers).

## Install

Requirements are an Apple-silicon Mac running macOS Tahoe 26 or later and
Apple [`container` 1.1.0](https://github.com/apple/container/releases/tag/1.1.0).
Install Apple's signed package first, then install `devcontainer`:

When macOS asks whether the selected runtime's `container-runtime-linux` may
find and connect to devices on the local network, choose **Allow**. Stock mode
uses Apple's signed helper; the optional provider stack uses its separately
installed helper. macOS can list them as distinct Local Network entries.
Denying either helper leaves its host listener open but resets connections with
`No route to host`.

```console
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install stephenlclarke/tap/devcontainer
/usr/local/bin/container system start
brew services start stephenlclarke/tap/devcontainer
devcontainer doctor --container /usr/local/bin/container
```

Use the compatibility socket only in the shell that needs it:

```console
eval "$(devcontainer context)"
npx --yes @devcontainers/cli@0.88.0 up \
  --workspace-folder /path/to/project
```

For VS Code, configure the Compose wrapper once and launch the workspace from
that configured shell:

```json
{
  "dev.containers.dockerComposePath": "/opt/homebrew/bin/devcontainer-compose"
}
```

```console
eval "$(devcontainer context)"
code /path/to/project
```

Optional Apple CLI plug-in registration is explicit and reversible:

```console
devcontainer plugin register
container devcontainer doctor
```

The stable formula installs this project with upstream Docker CLI and Docker
Compose protocol-client dependencies; it does not install a container runtime.
Plug-in registration is an explicit, reversible symlink into the active
runtime's reported install root, and it never replaces a foreign registration.
`container-compose` remains an explicit optional installation and provider
choice. See [INSTALL.md](INSTALL.md) for stock/custom runtime selection,
service management, upgrades, verification, troubleshooting, and removal.

## Independence and trademarks

This is an independent open-source project. It is not affiliated with or endorsed by Apple, Docker, Microsoft, or the Dev Containers maintainers. Apple, Docker, Visual Studio Code, and other marks belong to their respective owners.

## License

Licensed under [Apache License 2.0](LICENSE), matching `apple/container` and
`apple/containerization`. The package builder includes third-party notices,
deterministic build metadata, checksums, and an SPDX 2.3 SBOM.
