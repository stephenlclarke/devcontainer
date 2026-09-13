#!/usr/bin/env bash
# USAGE: verify-recovered-assets.sh ASSETS_DIRECTORY EXPECTED_COMMIT
#
# Authenticate an immutable published release before its bytes replace the
# freshly built, signed, and notarized candidate in a recovery run.

#===----------------------------------------------------------------------===#
# Copyright 2026 devcontainer project authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#===----------------------------------------------------------------------===#

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_NAME="$(basename "$SELF_PATH")"
readonly SCRIPT_NAME
REPOSITORY_ROOT="$(cd "$(dirname "$SELF_PATH")/../.." && pwd -P)"
readonly REPOSITORY_ROOT
readonly CODESIGN="${DEVCONTAINER_RECOVERY_CODESIGN:-codesign}"
readonly XCRUN="${DEVCONTAINER_RECOVERY_XCRUN:-xcrun}"
readonly COMMAND_TIMEOUT_SECONDS="${DEVCONTAINER_RECOVERY_TIMEOUT_SECONDS:-120}"
readonly DEADLINE_RUNNER="$REPOSITORY_ROOT/Tools/ci/run-command-with-deadline.py"
readonly PACKAGE_VERIFIER="$REPOSITORY_ROOT/Tools/release/verify-package.py"

usage() {
  printf 'usage: %s ASSETS_DIRECTORY EXPECTED_COMMIT\n' "$SCRIPT_NAME"
}

if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
  usage
  exit 0
fi
if (( $# != 2 )); then
  usage >&2
  exit 2
fi

readonly ASSETS_DIRECTORY="$1"
readonly EXPECTED_COMMIT="$2"
readonly SIGNING_IDENTITY="${DEVCONTAINER_SIGNING_IDENTITY:-}"
readonly NOTARY_PROFILE="${DEVCONTAINER_NOTARY_PROFILE:-}"
readonly NOTARY_KEYCHAIN="${DEVCONTAINER_NOTARY_KEYCHAIN:-}"

if [[ "$ASSETS_DIRECTORY" != /* || ! -d "$ASSETS_DIRECTORY" ||
      -L "$ASSETS_DIRECTORY" ]]; then
  printf 'recovered asset directory must be a safe absolute directory\n' >&2
  exit 2
fi
if [[ ! "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'recovered asset commit must be 40 lowercase hexadecimal characters\n' >&2
  exit 2
fi
if [[ ! "$SIGNING_IDENTITY" =~ ^[0-9A-Fa-f]{40}$ ]]; then
  printf 'recovered asset verification requires a Developer ID fingerprint\n' >&2
  exit 2
fi
if [[ -z "$NOTARY_PROFILE" ]]; then
  printf 'recovered asset verification requires a notary profile\n' >&2
  exit 2
fi
if [[ -n "$NOTARY_KEYCHAIN" ]] && {
  [[ "$NOTARY_KEYCHAIN" != /* ]] ||
    [[ ! -f "$NOTARY_KEYCHAIN" ]] ||
    [[ -L "$NOTARY_KEYCHAIN" ]]
}; then
  printf 'recovered asset notary keychain is unsafe\n' >&2
  exit 2
fi
if [[ ! "$COMMAND_TIMEOUT_SECONDS" =~ ^[1-9][0-9]{0,3}$ ||
      "$COMMAND_TIMEOUT_SECONDS" -gt 3600 ]]; then
  printf 'recovered asset command timeout must be 1 to 3600 seconds\n' >&2
  exit 2
fi
for command_name in "$CODESIGN" cmp find grep jq python3 shasum tar "$XCRUN"; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'recovered asset verification command is missing: %s\n' \
      "$command_name" >&2
    exit 2
  fi
done
if [[ ! -f "$DEADLINE_RUNNER" || -L "$DEADLINE_RUNNER" ||
      ! -f "$PACKAGE_VERIFIER" || -L "$PACKAGE_VERIFIER" ]]; then
  printf 'recovered asset verifier dependency is missing or unsafe\n' >&2
  exit 2
fi

run_bounded() {
  python3 "$DEADLINE_RUNNER" \
    --seconds "$COMMAND_TIMEOUT_SECONDS" -- "$@" < /dev/null
}

shopt -s nullglob
context_files=("$ASSETS_DIRECTORY"/*.context.json)
shopt -u nullglob
if (( ${#context_files[@]} != 1 )); then
  printf 'recovered release must contain exactly one package context\n' >&2
  exit 1
fi
readonly CONTEXT="${context_files[0]}"

if ! jq -e --arg commit "$EXPECTED_COMMIT" '
  .commit == $commit and
  (.asset | type == "string" and length > 0) and
  (.productVersion | type == "string" and
    test("^[0-9]+[.][0-9]+[.][0-9]+$")) and
  (.lane == "current" or .lane == "stable")
' "$CONTEXT" >/dev/null; then
  printf 'recovered package context does not match release authority\n' >&2
  exit 1
fi

ASSET="$(jq -er '.asset' "$CONTEXT")"
VERSION="$(jq -er '.productVersion' "$CONTEXT")"
LANE="$(jq -er '.lane' "$CONTEXT")"
readonly ASSET VERSION LANE
readonly ARCHIVE="$ASSETS_DIRECTORY/$ASSET"
readonly CHECKSUM="$ARCHIVE.sha256"
readonly VERIFICATION="$ARCHIVE.verification.json"
readonly BUILD_INFO="$ASSETS_DIRECTORY/build-info.json"
readonly SBOM="$ASSETS_DIRECTORY/devcontainer.spdx.json"
readonly NOTARIZATION="$ASSETS_DIRECTORY/notarization.json"

expected_names="$(
  printf '%s\n' \
    "$ASSET" \
    "$ASSET.sha256" \
    "$ASSET.context.json" \
    "$ASSET.verification.json" \
    build-info.json \
    devcontainer.spdx.json \
    notarization.json | sort
)"
actual_names="$(
  find "$ASSETS_DIRECTORY" -mindepth 1 -maxdepth 1 -type f \
    -exec basename {} \; | sort
)"
if [[ "$actual_names" != "$expected_names" ]]; then
  printf 'recovered release asset inventory is not exact\n' >&2
  exit 1
fi

temporary="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/devcontainer-recovery.XXXXXX")"
readonly temporary
cleanup() {
  find "$temporary" -depth -delete >/dev/null 2>&1 || true
}
trap cleanup EXIT

generated_verification="$temporary/verification.json"
python3 "$PACKAGE_VERIFIER" \
  --archive "$ARCHIVE" \
  --checksum "$CHECKSUM" \
  --expected-version "$VERSION" \
  --expected-lane "$LANE" \
  --expected-commit "$EXPECTED_COMMIT" \
  --resolved "$REPOSITORY_ROOT/Package.stock.resolved" \
  --license-manifest "$REPOSITORY_ROOT/Tools/release/dependency-licenses.stock.json" \
  --require-notarization \
  --output "$generated_verification"
cmp "$generated_verification" "$VERIFICATION"

root="devcontainer-$VERSION"
for metadata in \
  "build-info.json:$BUILD_INFO" \
  "devcontainer.spdx.json:$SBOM" \
  "notarization.json:$NOTARIZATION"; do
  member="${metadata%%:*}"
  external="${metadata#*:}"
  tar -xOf "$ARCHIVE" "$root/share/devcontainer/$member" \
    >"$temporary/$member"
  cmp "$temporary/$member" "$external"
done

tar -xzf "$ARCHIVE" -C "$temporary"
binaries=(
  "$temporary/$root/bin/devcontainer"
  "$temporary/$root/bin/devcontainer-docker"
  "$temporary/$root/bin/devcontainer-compose"
  "$temporary/$root/libexec/devcontainer-compose/bin/compose"
  "$temporary/$root/libexec/devcontainer-compose/resources/compose-normalizer"
  "$temporary/$root/bin/devcontainer-engine"
  "$temporary/$root/libexec/container/plugins/devcontainer/bin/devcontainer"
)
expected_fingerprint="$(tr '[:lower:]' '[:upper:]' <<<"$SIGNING_IDENTITY")"
readonly expected_fingerprint
index=0
for binary in "${binaries[@]}"; do
  run_bounded "$CODESIGN" --verify --strict --verbose=2 "$binary"
  signature_metadata="$temporary/codesign-$index.txt"
  run_bounded "$CODESIGN" -dv --verbose=4 "$binary" \
    >"$signature_metadata" 2>&1
  if ! grep -Eq 'flags=.*\(runtime\)' "$signature_metadata"; then
    printf 'recovered executable lacks hardened runtime: %s\n' "$binary" >&2
    exit 1
  fi
  certificate_prefix="$temporary/certificate-$index-"
  run_bounded "$CODESIGN" -d \
    --extract-certificates "$certificate_prefix" "$binary" >/dev/null 2>&1
  certificate="${certificate_prefix}0"
  if [[ ! -s "$certificate" ]]; then
    printf 'recovered executable certificate is missing: %s\n' "$binary" >&2
    exit 1
  fi
  fingerprint="$(shasum "$certificate" | awk '{print toupper($1)}')"
  if [[ "$fingerprint" != "$expected_fingerprint" ]]; then
    printf 'recovered executable Developer ID authority mismatch: %s\n' \
      "$binary" >&2
    exit 1
  fi
  ((index += 1))
done

notary_id="$(jq -er '.id' "$NOTARIZATION")"
notary_status="$(jq -er '.status' "$NOTARIZATION")"
if [[ ! "$notary_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ||
      "$notary_status" != Accepted ]]; then
  printf 'recovered notarization evidence is invalid\n' >&2
  exit 1
fi
notary_arguments=(
  notarytool info "$notary_id"
  --keychain-profile "$NOTARY_PROFILE"
  --output-format json
)
if [[ -n "$NOTARY_KEYCHAIN" ]]; then
  notary_arguments+=(--keychain "$NOTARY_KEYCHAIN")
fi
notary_result="$temporary/notary-info.json"
run_bounded "$XCRUN" "${notary_arguments[@]}" >"$notary_result"
normalized_notary_id="$(tr '[:upper:]' '[:lower:]' <<<"$notary_id")"
if ! jq -e --arg id "$normalized_notary_id" '
  (.id | ascii_downcase) == $id and .status == "Accepted"
' "$notary_result" >/dev/null; then
  printf 'Apple notarization authority did not confirm recovered package\n' >&2
  exit 1
fi
