# Homebrew Tap Documentation

<!-- markdownlint-disable MD013 -->

> Status: maintained source for the managed `devcontainer` section in
> `stephenlclarke/homebrew-tap`. Release automation updates the section between
> explicit markers with each signed, notarized, attested, and verified package.

## Dev Containers For Apple container

`devcontainer` provides Dev Containers compatibility for Apple's stock `container` runtime on Apple-silicon Macs running macOS Tahoe.

The next stable formula generated from the native release template installs this project's four public commands (`devcontainer`, `devcontainer-engine`, `devcontainer-compose`, and `devcontainer-docker`) plus the private Node/Dev Containers CLI runtime under `libexec`. The currently installed stable formula remains bound to its original verified archive until a new release is published. The formula does not install, remove, replace, relink, start, or stop:

- Apple's `container` package.
- A custom `container` runtime.
- `container-compose`.

The formula depends on the upstream Docker CLI and Docker Compose protocol
client. It does not install or start a Docker engine. Install Apple's stock
runtime separately from Apple before using the Apple backend.

### Stable

The stable formula follows immutable bare semantic releases:

```sh
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install --formula stephenlclarke/tap/devcontainer
/usr/local/bin/container system start
brew services start stephenlclarke/tap/devcontainer
devcontainer doctor --container /usr/local/bin/container
```

Stable assets use:

```text
devcontainer-release-arm64.tar.gz
devcontainer-release-arm64.tar.gz.sha256
devcontainer-release-arm64.tar.gz.context.json
devcontainer-release-arm64.tar.gz.verification.json
build-info.json
devcontainer.spdx.json
notarization.json
```

Homebrew infers the stable formula version from the immutable tag-bearing URL. The formula does not repeat a redundant explicit `version` declaration.

### Current

The opt-in Current formula is designed to follow the newest validated `main`
package:

```sh
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install --formula stephenlclarke/tap/devcontainer-current
```

Current uses:

- Mutable GitHub prerelease/tag: `current`
- Immutable candidate asset: `devcontainer-current-<sha12>-arm64.tar.gz`
- Monotonic formula version: `current.<github_run_number>.<sha12>`

As of 15 September 2026, automatic Current publication is disabled and the
published formula remains `current.89.b31e80b2b9c0`, backed by July source
`b31e80b2b9c09ecc73bb3badf9cd5cf16550a538`. It is not a package of the latest
source-bearing `main` revision. Stable 1.0.1 remains the immutable supported
baseline while a fresh Current package awaits complete release and runtime
evidence.

Stable and Current cannot coexist because they install the same executables. The optional Current formula declares a conflict with `devcontainer`; install one channel at a time and uninstall the active channel before switching.

### Requirements

Both formulae declare:

```ruby
depends_on arch: :arm64
depends_on "docker"
depends_on "docker-compose"
depends_on macos: :tahoe
```

Published ports require Local Network access for the selected runtime's
`container-runtime-linux` helper. Stock Apple and separately installed custom
runtime helpers may appear as distinct entries under **System Settings →
Privacy & Security → Local Network**; authorize each runtime that will be used.

Neither formula declares a dependency on:

```ruby
"stephenlclarke/tap/container"
"stephenlclarke/tap/container-current"
"stephenlclarke/tap/container-compose"
"stephenlclarke/tap/container-compose-current"
```

This separation is intentional. `devcontainer`'s supported core compatibility
boundary is Apple's stock runtime. The upstream Docker CLI and Docker Compose
are required protocol clients; a Docker engine and `container-compose` are
optional backends/providers.

### Verify

After installation:

```sh
devcontainer version
devcontainer version --format json
devcontainer --help
container system version --format json
```

The `devcontainer` output reports its source commit and release lane. The `container` output independently confirms which runtime the user selected.

### Optional Compose Provider

The tap does not install `container-compose` for `devcontainer`.

Current supported `stephenlclarke/tap/container-compose` formulae depend on a
matched custom runtime. They must not be installed automatically or described
as Compose support supplied by Apple. Users who deliberately configure a
provider are responsible for its installation and runtime compatibility;
`devcontainer` will report a custom runtime as a separate provider lane.

## Formula Publication Contract

The `devcontainer` source repository owns the formula template and release renderer. This tap owns the generated formula.

Release automation must:

1. Resolve a release-eligible exact source commit.
2. Require exact-commit CI, CodeQL, documentation, and live parity evidence.
3. Require Developer ID signing and an accepted Apple notary submission.
4. Publish a checksum, SPDX SBOM, build info, and GitHub provenance attestation.
5. Re-download the published archive and checksum.
6. Verify archive integrity, expected entries, signature, and checksum.
7. Render only `Formula/devcontainer.rb` or `Formula/devcontainer-current.rb`.
8. Run `ruby -c`, `brew style`, and strict audit.
9. Commit one Conventional Commit to the tap.
10. Install the formula, run `brew test`, and compare installed provenance with the selected commit.

Stable and Current tap updates share one non-cancelling concurrency group so they cannot race.

## Formula Shape

The installed release URL, version and checksum belong to its immutable release and change only when the tap publication transaction selects a new verified archive. The next native formula keeps the following install layout; the maintained source is [the formula template](../Tools/release/devcontainer.rb.in).

```ruby
def install
  bin.install "bin/devcontainer"
  bin.install "bin/devcontainer-engine"
  bin.install "bin/devcontainer-compose"
  bin.install "bin/devcontainer-docker"
  libexec.install "libexec/container"
  libexec.install "libexec/devcontainer"
  pkgshare.install "share/devcontainer"
end
```

The actual formula test checks all four public executables, reads the private Node and CLI versions from the installed runtime lock, verifies the runtime notices, and reads a deterministic configuration using the installed private CLI and a local Unix-socket inventory stub. Source-repository Homebrew checks render and lint the formula; they do not install it or establish native runtime qualification. The Current template changes the class, formula name, version, URL, and expected lane, and adds `conflicts_with "devcontainer"` while preserving the same complete runtime layout.

## Tap CI

Source-repository pull requests and pushes render a formula fixture and run:

```text
ruby -c
brew style
```

The protected publication transaction then runs against the staged,
Developer ID-signed and notarized release archive:

```text
brew audit --formula --strict --online
brew fetch --formula --force
brew install --formula
brew test
```

GitHub Actions must be pinned to complete commit SHAs. Tap CI must not start a live Apple VM merely to validate formula installation; live runtime compatibility belongs to the source repository's trusted bare-metal release gate.

## Devcontainer Source Maintenance

Do not add `devcontainer` as a tap submodule. The formula is generated directly from immutable release assets and a source-owned template. This avoids stale source snapshots and keeps the tap's live content limited to installation metadata.
