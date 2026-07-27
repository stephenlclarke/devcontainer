#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repository_root"

version="$(
  awk '$1 == "DEVCONTAINER_VERSION" && $2 == "?=" { print $3; exit }' \
    Makefile
)"
if [[ -z "$version" ]]; then
  printf 'DEVCONTAINER_VERSION is missing from Makefile\n' >&2
  exit 1
fi
if ! commit="$(git rev-parse --verify HEAD 2>/dev/null)"; then
  commit="unspecified"
fi
architecture="$(uname -m)"
if [[ "$architecture" != "arm64" ]]; then
  printf 'release packages require arm64; found %s\n' "$architecture" >&2
  exit 1
fi

dist="$repository_root/dist"
stage="$dist/stage/devcontainer-$version"
archive="$dist/devcontainer-$version-macos-arm64.tar.gz"
python3 Tools/ci/safe-package-path.py "$stage" "$dist"

rm -rf "$dist/stage"
mkdir -p \
  "$stage/bin" \
  "$stage/libexec/container/plugins/devcontainer" \
  "$stage/share/devcontainer"

GIT_COMMIT="$commit" DEVCONTAINER_BUILD_LANE=release \
  swift build --disable-automatic-resolution -c release

install -m 0755 .build/release/devcontainer "$stage/bin/devcontainer"
install -m 0755 .build/release/devcontainer-engine "$stage/bin/devcontainer-engine"
install -m 0755 .build/release/devcontainer-compose "$stage/bin/devcontainer-compose"
install -m 0755 .build/release/devcontainer \
  "$stage/libexec/container/plugins/devcontainer/container-devcontainer"
install -m 0644 LICENSE NOTICE.md README.md \
  "$stage/share/devcontainer/"
install -m 0644 Packaging/com.github.stephenlclarke.devcontainer.plist.in \
  "$stage/share/devcontainer/"

python3 Tools/release/write-build-info.py \
  --version "$version" \
  --commit "$commit" \
  --output "$stage/share/devcontainer/build-info.json"
python3 Tools/release/write-sbom.py \
  --version "$version" \
  --output "$stage/share/devcontainer/devcontainer.spdx.json"

tar -C "$dist/stage" -czf "$archive" "devcontainer-$version"
shasum -a 256 "$archive" >"$archive.sha256"
printf '%s\n' "$archive"
