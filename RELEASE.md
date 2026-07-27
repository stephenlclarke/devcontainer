# Release Design

<!-- markdownlint-disable MD013 -->

> Status: implementation in progress. The authoritative version, selector resolver, Current version formatter, deterministic package, checksums, SBOM, formula renderer, hosted CI, quality, CodeQL, dependency review, OpenSSF Scorecard, Homebrew, SonarQube, and DocC workflows are implemented. Stable/Current publication, signing, notarization, tap promotion, and the trusted release runner remain release blockers; later sections distinguish the implemented foundation from those required publication controls.

This document defines how `devcontainer` will validate and publish an arm64 macOS command-line tool for Apple's stock `container` runtime. Docker is the behavioral oracle, stock Apple `container` is the required runtime, and `container-compose` is an optional provider tested in a separate, explicitly identified parity lane.

## Release Principles

- `main` is the releasable integration branch.
- One checked-in value, `DEVCONTAINER_VERSION` in `Makefile`, is the authoritative product version.
- Bare `MAJOR.MINOR.PATCH` tags are immutable stable release identities.
- `current` is the only mutable tag and points to the newest release-eligible `main` commit.
- A package is authorized by an exact commit, never by a branch name alone.
- Live Docker, stock Apple, and Compose-provider parity runs only on trusted bare-metal Apple silicon.
- GitHub-hosted macOS validates source, tests, coverage, package structure, formula rendering, and documentation, but is not accepted as live Virtualization.framework evidence.
- Releases never install, replace, or start a custom `container` runtime as a side effect.
- Releases never install `container-compose` as a side effect.
- Missing runtime or provider prerequisites fail the strict release gate; they are not reported as successful skips.
- Stable assets, tags, notes, checksums, SBOMs, and formula versions are immutable.
- GitHub Actions are pinned to complete commit SHAs, with the readable release version retained in a comment.
- Prebuilt macOS artifacts are Developer ID signed and submitted to Apple's notary service before publication.
- Every package publishes build metadata, a checksum, an SBOM, and GitHub build provenance.

## Version Authority

The version mechanism deliberately follows `container-compose`'s current release model:

- The product version is read from a Makefile assignment.
- Stable version selectors are resolved relative to the newest bare semantic tag.
- `--+`, `-+-`, and `+--` mean patch, minor, and major increments.
- `current` uses a monotonically increasing Homebrew version based on the Actions run number and source SHA.
- Stable publication requires the checked-in product version to equal the semantic tag.
- Stable tags are annotated, SSH-signed, pushed, and verified by GitHub before package publication.

The `devcontainer` adaptation removes `container-compose`'s duplicated version literals. The only tracked product-version declaration will be:

```makefile
DEVCONTAINER_VERSION ?= 0.1.0
```

Source code must not contain a second editable copy. Build and package targets will read `DEVCONTAINER_VERSION` and generate an untracked build-info resource. The executable's `version` command will read that generated resource, with an explicit `development` fallback for an unpackaged local build.

The implemented helper layout is:

```text
Makefile
Tools/release/current-formula-version.py
Tools/release/devcontainer.rb.in
Tools/release/package-context.py
Tools/release/publish-github-release.sh
Tools/release/release-version.py
Tools/release/render-homebrew-formula.py
Tools/release/sign-and-notarize.sh
Tools/release/update-tap-readme.py
Tools/release/verify-package.py
Tools/release/write-build-info.py
Tools/release/write-notarization-evidence.py
```

`Tools/release/release-version.py` is the reusable implementation of selector parsing and validation. It:

- Read `DEVCONTAINER_VERSION` from `Makefile`.
- List only tags matching `[0-9]+\.[0-9]+\.[0-9]+`.
- Sort numeric components rather than lexical strings.
- Resolve an explicit version or exactly one `+` in a three-character selector.
- Reject a requested version that is not newer than the newest stable tag.
- Reject a requested version lower than the checked-in product version.
- Update only the Makefile declaration during stable release preparation.

Selectors retain `container-compose` semantics:

| Selector | Meaning | Example from `1.4.2` |
| --- | --- | --- |
| `--+` | Patch | `1.4.3` |
| `-+-` | Minor | `1.5.0` |
| `+--` | Major | `2.0.0` |
| `2.1.0` | Explicit semantic version | `2.1.0` |

Version resolution is read-only:

```sh
make release-version VERSION_SELECTOR=-+-
```

Stable release preparation is the only version target that mutates the Makefile:

```sh
make prepare-release VERSION_SELECTOR=-+-
```

The final stable release orchestration entry point will be:

```sh
DEVCONTAINER_RELEASE_INTENT=milestone make release VERSION_SELECTOR=-+-
```

`milestone`, `maintenance`, and `security` will be the accepted intents. Maintenance is a documented patch release. Security requires an advisory or incident reference. Milestone releases will require the selected Current build to have soaked for seven days unless an explicit, recorded milestone-only override is authorized. No override may bypass validation, signing, notarization, parity, or exact-commit authority.

### Current Channel

Every eligible successful CI run for the newest `main` commit may refresh:

- Mutable lightweight tag: `current`
- Mutable prerelease title: `Current build`
- Commit-identified asset: `devcontainer-current-<sha12>-arm64.tar.gz`
- Formula: `devcontainer-current.rb`
- Homebrew version: `current.<github_run_number>.<sha12>`

The formula version will use the same validated algorithm as `container-compose`:

```text
current.418.0123456789ab
```

Automatic Current publication is gated by the repository variable `DEVCONTAINER_CURRENT_PUBLISH_ENABLED=true`. It remains disabled until a repository-scoped runner with the `devcontainer-release` label, a Developer ID Application identity, the configured notary profile, and the tap token are all provisioned. Manual dispatch remains fail-closed against the same prerequisites.

The full source identity remains the lowercase 40-character commit SHA. The 12-character prefix is only a display and asset-name convenience.

Current publication must be staged safely:

1. Build immutable commit-identified assets.
2. Sign, notarize, validate, checksum, inventory, and attest those assets.
3. Upload candidate assets to the existing Current prerelease without moving `current`.
4. Render and commit `devcontainer-current.rb` using the commit-identified URL and checksum.
5. Install and test that formula.
6. Recheck that the candidate is still the remote `main` head.
7. Move the unsigned lightweight `current` tag.
8. Finalize the Current release object.
9. Remove superseded Current assets only after the new channel is verified.

This ordering keeps the old Current formula valid if publication is interrupted.

### Stable Channel

A stable release uses:

- Annotated, SSH-signed bare tag: `MAJOR.MINOR.PATCH`
- Release title: `MAJOR.MINOR.PATCH`
- Immutable asset: `devcontainer-release-arm64.tar.gz`
- Formula: `devcontainer.rb`
- Homebrew version: `MAJOR.MINOR.PATCH`

Stable publication requires:

- The tag is the newest semantic source tag.
- The tag resolves to the prepared source commit.
- GitHub reports the annotated tag signature as verified.
- `DEVCONTAINER_VERSION` equals the tag.
- The release object does not already exist, except for an explicit formula-only recovery.
- Exact-commit CI, CodeQL, dependency review, Scorecard, documentation, and hosted release checks succeeded.
- The trusted live parity gate succeeded for the same commit.
- The candidate-bound `Stable Release Authority (MAJOR.MINOR.PATCH)` check succeeded.
- Signing, notarization, SBOM generation, package validation, checksum verification, attestation, and Homebrew installation succeeded.

An existing stable release is immutable. Recovery may recreate only a missing or incorrect Homebrew formula from already published, re-downloaded, checksum-verified assets. It must never retag, rebuild, replace, or use `--clobber` on stable assets.

## Generated Build Information

`make package-release` generates `build-info.json` from the authoritative version and release inputs. The minimum payload is:

```json
{
  "buildType": "release",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "containerDistribution": "apple",
  "containerVersion": "0.7.0",
  "lane": "stable",
  "provider": "none",
  "source": "stephenlclarke/devcontainer",
  "version": "1.0.0"
}
```

Current packages use the same product version but set `lane` to `current`. Provider parity evidence records the provider executable, provider version, and underlying runtime distribution separately; it must not rewrite the package's stock-Apple identity.

`devcontainer version --format json` exposes this payload. Release validation compares its version, lane, commit, architecture, and source with the selected release context.

## CI And Release Authority

The implemented workflow split is:

| Workflow | Runner | Purpose |
| --- | --- | --- |
| `ci.yml` | Ubuntu and `macos-26` | Source checks, unit tests, coverage, CLI smoke, package structure, and Sonar |
| `codeql.yml` | Appropriate hosted runner | Exact-commit CodeQL analysis |
| `dependency-review.yml` | Hosted Ubuntu | Exact-range vulnerability and Apache-compatible license review |
| `scorecard.yml` | Hosted Ubuntu | OpenSSF analysis, authenticated result publication, and SARIF upload |
| `quality.yml` | Hosted macOS | Sanitizers, style, and scheduled deeper checks |
| `docs.yml` | `macos-26`, then Ubuntu | Build and publish DocC Pages |
| `homebrew.yml` | `macos-26` | Render fixture formulae, `ruby -c`, `brew style`, audit, install, and test |
| `parity.yml` | Trusted bare-metal Apple silicon | Live Docker, stock Apple, and Compose-provider parity |
| `stable-release-gate.yml` | Ubuntu and hosted macOS | Resolve immutable candidate and record release authority |
| `prebuilt-binaries.yml` | Trusted bare-metal Apple silicon | Sign, notarize, package, attest, release, and update the tap |

```mermaid
flowchart TD
    Change["Pull request or protected main change"] --> HostedCI["Hosted CI, coverage, CodeQL, and package checks"]
    Change --> Docs["DocC build"]
    HostedCI --> Aggregate["Stable exact-commit Validate authority"]
    Docs --> Aggregate
    Aggregate --> MainCheck{"Newest remote main commit?"}
    MainCheck -->|No| Superseded["Stop without publishing"]
    MainCheck -->|Yes| BareMetal["Trusted bare-metal Apple parity gate"]
    BareMetal --> Docker["Docker oracle lane"]
    BareMetal --> Apple["Stock Apple container lane"]
    BareMetal --> Compose["Explicit container-compose provider lane"]
    Docker --> Parity["Normalized three-lane parity report"]
    Apple --> Parity
    Compose --> Parity
    Parity --> Current["Stage signed and notarized Current assets"]
    Current --> CurrentTap["Update and verify devcontainer-current formula"]
    CurrentTap --> MoveCurrent["Move current tag and finalize prerelease"]
    MoveCurrent --> Soak["Current soak and release intent"]
    Soak --> SignedTag["Prepare version and create verified SSH-signed semver tag"]
    SignedTag --> HostedGate["Hosted immutable stable gate"]
    HostedGate --> Authority["Stable Release Authority check on candidate SHA"]
    Authority --> StableBuild["Rebuild tagged source on trusted release runner"]
    StableBuild --> SupplyChain["Sign, notarize, SBOM, checksum, and attest"]
    SupplyChain --> StableRelease["Publish immutable GitHub Release"]
    StableRelease --> StableTap["Update, install, and verify stable Homebrew formula"]
```

### Stable Aggregate Checks

Branch protection should require stable context names:

- `Validate`
- `CodeQL`
- `Documentation`

Heavy and documentation-only paths must both conclude through a real `Validate` job. A no-op path may report why an expensive analysis was unnecessary, but the aggregate must fail if no accepted validation path completed.

Every release resolver must query runs by exact commit and inspect final job and step conclusions. A workflow-level success is insufficient when a required step may have skipped. Current publication should require an exact-main successful Sonar step when Sonar is enabled; transient API failures must fail closed rather than masquerade as absent evidence.

### Candidate-Bound Stable Authority

`stable-release-gate.yml` will:

1. Validate the bare semantic input.
2. Resolve the signed tag to a 40-character commit.
3. Verify the GitHub tag object's signature.
4. Verify exact-commit CI, CodeQL, documentation, and parity authorities.
5. Checkout the candidate and release-control revision separately.
6. Run the hosted release gate without live virtualization.
7. Create `Stable Release Authority (MAJOR.MINOR.PATCH)` on the candidate commit with the gate run as its details URL.

The package workflow must find that exact named check on the selected commit and verify its source workflow, event, completion, and successful conclusion.

## GitHub Actions Supply-Chain Policy

Actions must be pinned to full commit SHAs. The initial implementation may reuse the currently reviewed pins from the sibling release workflows:

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
- uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6
- uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7
- uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8
- uses: github/codeql-action/init@e4fba868fa4b1b91e1fdab776edc8cfbe6e9fb81 # v4
- uses: github/codeql-action/analyze@e4fba868fa4b1b91e1fdab776edc8cfbe6e9fb81 # v4
- uses: actions/attest-build-provenance@0f67c3f4856b2e3261c31976d6725780e5e4c373 # v4.1.1
```

Dependabot should update the grouped GitHub Actions ecosystem weekly. Any new action must be reviewed and pinned before merge.

Permissions begin at read-only and escalate per job. The attestation job alone receives `id-token: write` and `attestations: write`; the release job alone receives `contents: write`; the stable-authority job alone receives `checks: write`; and the Pages deployment receives `pages: write` and `id-token: write`.

Self-hosted workflows must never run pull-request code. They accept only protected `main`, a GitHub-verified stable tag, or an explicit trusted dispatch. The release runner registration is repository-scoped.

## Trusted Bare-Metal Parity Gate

The live gate requires:

```yaml
runs-on: [self-hosted, macOS, ARM64, devcontainer-release]
```

The runner must provide:

- Apple silicon.
- A supported macOS Tahoe release with `kern.hv_support=1`.
- A deliberately installed stock Apple `container` distribution.
- A deliberately installed Docker engine and pinned Docker Compose oracle.
- An explicitly configured `container-compose` executable for the provider lane.
- `gh`, `git`, `jq`, `make`, `python3`, `shasum`, `ssh-keygen`, `swift`, and `tar`.
- A noninteractive Developer ID signing identity.
- A `notarytool` profile in the local keychain.
- Git identity and SSH signing configuration for release commits and tags.

The gate must capture versions and executable paths before testing. It must reject a stock lane when `container system version --format json` identifies a custom distribution.

The three lanes are:

| Lane | Runtime | Compose | Release meaning |
| --- | --- | --- | --- |
| Docker oracle | Pinned Docker engine | Pinned Docker Compose | Expected Dev Containers behavior |
| Stock Apple | Apple-signed `container` | None | Required core compatibility |
| Compose provider | Explicitly supplied runtime and `container-compose` | Required | Optional multi-service integration evidence |

The provider lane must state whether its underlying runtime is `apple` or `custom`. Current supported `stephenlclarke/tap/container-compose` depends on a custom matched runtime, so installing that formula is forbidden in the stock lane. Until the provider works against stock Apple, its live evidence is valid only as a separately labelled provider comparison and cannot be used to claim stock-Apple Compose support.

Each lane uses:

- A unique project and resource prefix.
- An isolated, marked runtime state directory.
- Explicit binary paths.
- A cleanup trap.
- A preflight that fails in strict mode.
- Deterministic fixtures.
- Normalized JSON results.
- Sequential execution on a shared host.

The aggregate release gate fails if any required lane is unavailable, the Docker oracle version differs from its pin, stock Apple is replaced by a custom distribution, cleanup fails materially, or an undocumented parity difference appears.

### Upstream Stephen-Stack Defects

Future parity work may identify a defect owned by `stephenlclarke/container`, `stephenlclarke/containerization`, `stephenlclarke/container-builder-shim`, or `stephenlclarke/container-compose`. Fix and test that defect through a focused pull request in the owning repository. `devcontainer` may consume the result only by recording and validating the exact reviewed commit.

Never carry a silent local patch, unpublished worktree state, or ad hoc fork modification in a parity or release build. Never publish an unreviewed cross-repository change from `devcontainer` release automation. Cross-repository source promotion remains the owning repository's reviewed responsibility.

## Documentation And DocC Pages

`docs.yml` will build the package's DocC site on `macos-26`. Pull requests build and upload an ordinary artifact. Only protected `main` in `stephenlclarke/devcontainer` may upload and deploy the Pages artifact.

Use full-SHA pins:

```yaml
- uses: actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d # v6
- uses: actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5
- uses: actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128 # v5
```

The workflow should use a stable `Documentation` aggregate context and a deployment environment named `github-pages`. Documentation publication is independent of package publication but remains exact-commit evidence for stable releases.

## Signing And Notarization

Source identity and binary identity are separate requirements:

- Release-preparation commits are SSH-signed.
- Stable annotated tags are SSH-signed and GitHub-verified.
- The arm64 Mach-O executable is signed with a Developer ID Application identity, hardened runtime, and secure timestamp.
- The exact signed executable is submitted to Apple's notary service.
- GitHub provenance attests the final distributed archive and SBOM.

The release runner should hold signing and notarization material in the macOS keychain. Private keys, certificates, API keys, and notary credentials must not be copied into pull-request workflows, logs, artifacts, repository files, or generic Actions secrets.

Planned verification includes:

```sh
codesign --verify --strict --verbose=2 /path/to/devcontainer
xcrun notarytool submit /path/to/notarization.zip --keychain-profile devcontainer-release --wait
```

A standalone Mach-O executable cannot be stapled like an app, pkg, or dmg. The release must not claim stapling unless the final distribution format changes to one supported by `stapler`. For the planned tarball, submit a ZIP containing the exact signed binary for notarization, then create the release tarball from those unchanged signed bytes. Record the notary submission identifier and accepted status in release evidence without publishing credentials.

After downloading the published asset, validate the signature again and run an appropriate Gatekeeper assessment on a clean test account or host.

## SBOM, Checksums, And Attestation

Every Current and stable package publishes:

- `devcontainer-<lane>-arm64.tar.gz`
- Matching `.sha256`
- `devcontainer-sbom.spdx.json`
- `build-info.json`
- Release notes with exact CI, CodeQL, documentation, parity, signing, and notarization links

The SBOM generator and version will be pinned and its download checksum verified before first use. The SBOM must cover the release archive, executable, Swift package dependencies, bundled resources, licenses, and build tool identity. Package validation rejects an empty or unparsable SBOM.

`actions/attest-build-provenance` will attest the final archive, checksum, SBOM, and build-info assets. The release notes will show the `gh attestation verify` command for the published repository.

Checksums are calculated only after signing and notarization, because those bytes are the distributed identity. Formula rendering downloads the published archive and checksum again, verifies both, checks required archive entries, and derives the Homebrew SHA from that verified archive.

## Homebrew Design

The producer repository owns the formula template and renderer. The tap owns the generated live formula.

Stable formula:

```ruby
class Devcontainer < Formula
  desc "Dev Containers compatibility for Apple's container runtime"
  homepage "https://github.com/stephenlclarke/devcontainer"
  url "https://github.com/stephenlclarke/devcontainer/releases/download/0.1.0/devcontainer-release-arm64.tar.gz"
  version "0.1.0"
  sha256 "RELEASE_SHA256"
  license "Apache-2.0"

  depends_on arch: :arm64
  depends_on macos: :tahoe

  def install
    bin.install "bin/devcontainer"
  end

  def caveats
    <<~EOS
      This formula installs only devcontainer.
      Install Apple's stock container runtime separately from Apple.
      Docker and container-compose are optional comparison/providers and are not installed or replaced.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/devcontainer version --short")
    assert_match "Usage", shell_output("#{bin}/devcontainer --help")
  end
end
```

The Current formula uses `DevcontainerCurrent`, a commit-identified URL, and `current.<run>.<sha12>`. Stable and Current formulae must conflict because both install `devcontainer`.

The formula must not:

- Depend on `stephenlclarke/tap/container`.
- Depend on `stephenlclarke/tap/container-compose`.
- Install, unlink, remove, or replace `/usr/local/bin/container`.
- Start or stop the Apple runtime.
- Add a `container-compose` plugin link.
- Mutate Apple package receipts, launch daemons, or user runtime data.

Tap updates are serialized and use a dedicated token. Automation verifies the tap push remote, commits only the intended formula, pushes one Conventional Commit, waits for tap CI, installs the formula, runs `brew test`, and compares the installed binary's build info with the selected commit.

## Make Target Roadmap

```text
all
workflow
ci
ci-fast
check
lint
format
test
coverage
coverage-check
build
build-release
cli-smoke
cli-smoke-built
docs
package
package-release
package-debug
package-validate
parity-docker
parity-apple
parity-compose-provider
parity-report
release-gate
release-gate-hosted
release-plan
release
sonar-scan
clean
```

Actions invoke these targets instead of duplicating release logic in YAML.

## Implementation Readiness Checklist

- [x] Add `DEVCONTAINER_VERSION` to `Makefile` as the sole tracked version.
- [x] Implement and test exact selector behavior.
- [x] Implement and test monotonic Current formula versions.
- [x] Implement generated build-info and `version --format json`.
- [x] Add immutable Current and stable package naming.
- [x] Add exact-commit CI and CodeQL workflows.
- [x] Add exact-range dependency review and OpenSSF Scorecard workflows.
- [ ] Add the trusted three-lane bare-metal parity runner and strict preflight.
- [x] Add DocC build and Pages deployment.
- [x] Add strict Developer ID signing, notarization, and sanitized evidence tooling.
- [ ] Provision the release identity/profile and record an accepted package submission.
- [x] Add deterministic SPDX SBOM generation, portable checksums, and strict package verification.
- [x] Add GitHub artifact attestation to the trusted publication workflow.
- [x] Add formula template, local renderer, syntax validation, and style validation.
- [x] Add immutable stable and staged/finalized Current publication workflows.
- [x] Add serialized tap formula and managed README promotion.
- [ ] Protect `main` with signed commits and stable required contexts.
- [x] Enable Dependabot security updates, vulnerability alerts, secret scanning, push protection, and private vulnerability reporting.
- [x] Add a private-reporting security policy.
- [ ] Publish the first Current build.
- [ ] Soak and promote the first stable release.
