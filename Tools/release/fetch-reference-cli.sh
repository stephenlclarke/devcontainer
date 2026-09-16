#!/usr/bin/env bash
# USAGE:
#   fetch-reference-cli.sh VERSION SHA256 OUTPUT_DIRECTORY
#
# Fetch and verify the pinned, self-contained upstream Dev Containers CLI.

set -euo pipefail

if (( $# != 3 )); then
  printf 'usage: %s VERSION SHA256 OUTPUT_DIRECTORY\n' "$(basename "$0")" >&2
  exit 64
fi

readonly VERSION="$1"
readonly EXPECTED_SHA256="$2"
readonly OUTPUT_DIRECTORY="$3"
readonly ARCHIVE_URL="https://registry.npmjs.org/@devcontainers/cli/-/cli-${VERSION}.tgz"
TEMPORARY_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/devcontainer-reference-cli.XXXXXX")"
readonly TEMPORARY_DIRECTORY
readonly ARCHIVE="$TEMPORARY_DIRECTORY/cli.tgz"

# Remove only the private directory returned by mktemp.
cleanup() {
  rm -rf "$TEMPORARY_DIRECTORY"
}
trap cleanup EXIT

curl --fail --location --silent --show-error \
  --proto '=https' --tlsv1.2 \
  "$ARCHIVE_URL" \
  --output "$ARCHIVE"

ACTUAL_SHA256="$(shasum -a 256 "$ARCHIVE" | awk '{ print $1 }')"
readonly ACTUAL_SHA256
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  printf 'reference CLI checksum mismatch: expected %s, found %s\n' \
    "$EXPECTED_SHA256" "$ACTUAL_SHA256" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIRECTORY"
tar -xzf "$ARCHIVE" -C "$OUTPUT_DIRECTORY" --strip-components=1

ACTUAL_VERSION="$(
  python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' \
    "$OUTPUT_DIRECTORY/package.json"
)"
readonly ACTUAL_VERSION
if [[ "$ACTUAL_VERSION" != "$VERSION" ]]; then
  printf 'reference CLI version mismatch: expected %s, found %s\n' \
    "$VERSION" "$ACTUAL_VERSION" >&2
  exit 1
fi

chmod -R u=rwX,go=rX "$OUTPUT_DIRECTORY"
