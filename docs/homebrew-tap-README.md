# Homebrew Tap Documentation

<!-- markdownlint-disable MD013 -->

> Status: proposed documentation for a future `stephenlclarke/homebrew-tap` update. No `devcontainer` formula exists in the tap yet, and the install commands below are not currently available.

This section is intended to be merged into the tap README when the first signed, notarized, attested, and verified `devcontainer` package is published.

## Dev Containers For Apple container

`devcontainer` provides Dev Containers compatibility for Apple's stock `container` runtime on Apple-silicon Macs running macOS Tahoe.

The formula installs only the `devcontainer` command. It does not install, remove, replace, relink, start, or stop:

- Apple's `container` package.
- A custom `container` runtime.
- Docker.
- `container-compose`.

Install Apple's stock runtime separately from Apple before using the Apple backend.

### Stable

The stable formula follows immutable bare semantic releases:

```sh
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install --formula stephenlclarke/tap/devcontainer
```

Stable assets use:

```text
devcontainer-release-arm64.tar.gz
devcontainer-release-arm64.tar.gz.sha256
devcontainer-sbom.spdx.json
build-info.json
```

The formula version exactly matches the GitHub release tag.

### Current

The opt-in Current formula follows the newest validated `main` package:

```sh
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install --formula stephenlclarke/tap/devcontainer-current
```

Current uses:

- Mutable GitHub prerelease/tag: `current`
- Immutable candidate asset: `devcontainer-current-<sha12>-arm64.tar.gz`
- Monotonic formula version: `current.<github_run_number>.<sha12>`

Stable and Current conflict because they install the same executable. Install one channel at a time.

### Requirements

Both formulae declare:

```ruby
depends_on arch: :arm64
depends_on macos: :tahoe
```

Neither formula declares a dependency on:

```ruby
"stephenlclarke/tap/container"
"stephenlclarke/tap/container-current"
"stephenlclarke/tap/container-compose"
"stephenlclarke/tap/container-compose-current"
```

This separation is intentional. `devcontainer`'s supported core compatibility boundary is Apple's stock runtime. Docker and `container-compose` are explicit optional backends/providers.

### Verify

After a formula is published and installed:

```sh
devcontainer version
devcontainer version --format json
devcontainer --help
container system version --format json
```

The `devcontainer` output reports its source commit and release lane. The `container` output independently confirms which runtime the user selected.

### Optional Compose Provider

The tap does not install `container-compose` for `devcontainer`.

Current supported `stephenlclarke/tap/container-compose` formulae depend on a matched custom runtime. They must not be installed automatically or described as stock-Apple Compose support. Users who deliberately configure a provider are responsible for its installation and runtime compatibility; `devcontainer` will report a custom runtime as a separate provider lane.

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

## Planned Formula Shape

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
      Docker and container-compose remain optional and are not installed or replaced.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/devcontainer version --short")
    assert_match "Usage", shell_output("#{bin}/devcontainer --help")
  end
end
```

The Current template changes the class, formula name, version, URL, and expected lane while preserving the runtime-neutral install.

## Tap CI

Changes to either formula should run on a hosted Tahoe-capable macOS runner:

```text
ruby -c
brew style
brew audit --formula --strict --online
brew fetch --formula --force
brew install --formula
brew test
brew info --formula
```

GitHub Actions must be pinned to complete commit SHAs. Tap CI must not start a live Apple VM merely to validate formula installation; live runtime compatibility belongs to the source repository's trusted bare-metal release gate.

## Source Maintenance

Do not add `devcontainer` as a tap submodule. The formula is generated directly from immutable release assets and a source-owned template. This avoids stale source snapshots and keeps the tap's live content limited to installation metadata.
