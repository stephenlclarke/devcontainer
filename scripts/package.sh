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
lane="${DEVCONTAINER_PACKAGE_LANE:-development}"
run_number="${DEVCONTAINER_PACKAGE_RUN_NUMBER:-}"
architecture="$(uname -m)"
if [[ "$architecture" != "arm64" ]]; then
  printf 'release packages require arm64; found %s\n' "$architecture" >&2
  exit 1
fi
source_date_epoch="${SOURCE_DATE_EPOCH:-}"
if [[ -z "$source_date_epoch" ]]; then
  if [[ "$commit" =~ ^[0-9a-f]{40}$ ]]; then
    source_date_epoch="$(git show -s --format=%ct "$commit")"
  else
    source_date_epoch=0
  fi
fi
if [[ ! "$source_date_epoch" =~ ^[0-9]+$ ]]; then
  printf 'SOURCE_DATE_EPOCH must be a non-negative integer\n' >&2
  exit 1
fi

dist="$repository_root/dist"
context="$dist/package-context.json"
mkdir -p "$dist"
context_arguments=(
  --product-version "$version"
  --lane "$lane"
  --commit "$commit"
)
if [[ -n "$run_number" ]]; then
  context_arguments+=(--run-number "$run_number")
fi
python3 Tools/release/package-context.py "${context_arguments[@]}" >"$context"
archive_name="$(
  python3 -c \
    'import json,sys; from pathlib import Path; print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["asset"])' \
    "$context"
)"
stage="$dist/stage/devcontainer-$version"
archive="$dist/$archive_name"
cp "$context" "$archive.context.json"
python3 Tools/ci/safe-package-path.py "$stage" "$dist"

rm -rf "$dist/stage"
mkdir -p \
  "$stage/bin" \
  "$stage/libexec/container/plugins/devcontainer/bin" \
  "$stage/share/devcontainer"

GIT_COMMIT="$commit" DEVCONTAINER_BUILD_LANE="$lane" \
  swift build --disable-automatic-resolution \
    -Xswiftc -warnings-as-errors \
    -c release

install -m 0755 .build/release/devcontainer "$stage/bin/devcontainer"
install -m 0755 .build/release/devcontainer-engine "$stage/bin/devcontainer-engine"
install -m 0755 .build/release/devcontainer-compose "$stage/bin/devcontainer-compose"
install -m 0755 .build/release/devcontainer \
  "$stage/libexec/container/plugins/devcontainer/bin/devcontainer"
install -m 0644 Packaging/devcontainer-plugin-config.toml \
  "$stage/libexec/container/plugins/devcontainer/config.toml"
install -m 0644 LICENSE NOTICE.md "$stage/share/devcontainer/"
python3 Tools/release/render-package-readme.py \
  --source README.md \
  --repository-root "$repository_root" \
  --repository stephenlclarke/devcontainer \
  --revision "$commit" \
  --output "$stage/share/devcontainer/README.md"
chmod 0644 "$stage/share/devcontainer/README.md"
install -m 0644 Packaging/com.github.stephenlclarke.devcontainer.plist.in \
  "$stage/share/devcontainer/"

python3 Tools/release/write-build-info.py \
  --version "$version" \
  --commit "$commit" \
  --lane "$lane" \
  --architecture "$architecture" \
  --output "$stage/share/devcontainer/build-info.json"
python3 Tools/release/write-sbom.py \
  --version "$version" \
  --commit "$commit" \
  --source-date-epoch "$source_date_epoch" \
  --output "$stage/share/devcontainer/devcontainer.spdx.json"
python3 Tools/release/write-third-party-notices.py \
  --checkouts .build/checkouts \
  --output "$stage/share/devcontainer/THIRD-PARTY-NOTICES.txt"

if [[ "${DEVCONTAINER_SIGNING_REQUIRED:-0}" == "1" ]]; then
  Tools/release/sign-and-notarize.sh \
    "$stage" \
    "$stage/share/devcontainer/notarization.json"
fi

python3 Tools/release/create-reproducible-archive.py \
  --source "$stage" \
  --output "$archive" \
  --epoch "$source_date_epoch"
archive_digest="$(shasum -a 256 "$archive" | awk '{ print $1 }')"
printf '%s  %s\n' "$archive_digest" "$(basename "$archive")" >"$archive.sha256"
verification_arguments=(
  --archive "$archive"
  --checksum "$archive.sha256"
  --expected-version "$version"
  --expected-lane "$lane"
  --expected-commit "$commit"
  --output "$archive.verification.json"
)
if [[ "${DEVCONTAINER_SIGNING_REQUIRED:-0}" == "1" ]]; then
  verification_arguments+=(--require-notarization)
fi
python3 Tools/release/verify-package.py "${verification_arguments[@]}"
printf '%s\n' "$archive"
