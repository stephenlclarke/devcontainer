#!/usr/bin/env bash
# USAGE:
#   sign-and-notarize.sh STAGE_DIRECTORY EVIDENCE_FILE
#
# Developer ID sign every packaged executable, submit their exact staged bytes
# to Apple's notary service, and retain sanitized acceptance evidence.

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

# Print the command-line interface.
usage() {
  printf 'usage: %s STAGE_DIRECTORY EVIDENCE_FILE\n' "$SCRIPT_NAME"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if (( $# != 2 )); then
  usage >&2
  exit 2
fi

readonly STAGE_DIRECTORY="$1"
readonly EVIDENCE_FILE="$2"
readonly SIGNING_IDENTITY="${DEVCONTAINER_SIGNING_IDENTITY:-}"
readonly SIGNING_KEYCHAIN="${DEVCONTAINER_SIGNING_KEYCHAIN:-}"
readonly NOTARY_PROFILE="${DEVCONTAINER_NOTARY_PROFILE:-}"
readonly NOTARY_KEYCHAIN="${DEVCONTAINER_NOTARY_KEYCHAIN:-}"
readonly COMMAND_TIMEOUT_SECONDS="${DEVCONTAINER_SIGNING_TIMEOUT_SECONDS:-120}"
readonly NOTARY_TIMEOUT_SECONDS="${DEVCONTAINER_NOTARY_TIMEOUT_SECONDS:-2100}"
readonly DEADLINE_RUNNER="$REPOSITORY_ROOT/Tools/ci/run-command-with-deadline.py"

if [[ -z "$SIGNING_IDENTITY" ]]; then
  printf 'DEVCONTAINER_SIGNING_IDENTITY is required\n' >&2
  exit 2
fi
if [[ -z "$SIGNING_KEYCHAIN" || "$SIGNING_KEYCHAIN" != /* ||
  ! -f "$SIGNING_KEYCHAIN" || -L "$SIGNING_KEYCHAIN" ]]; then
  printf 'DEVCONTAINER_SIGNING_KEYCHAIN must be a safe absolute keychain path\n' >&2
  exit 2
fi
if [[ -z "$NOTARY_PROFILE" ]]; then
  printf 'DEVCONTAINER_NOTARY_PROFILE is required\n' >&2
  exit 2
fi
if [[ -n "$NOTARY_KEYCHAIN" ]] && {
  [[ "$NOTARY_KEYCHAIN" != /* ]] ||
    [[ ! -f "$NOTARY_KEYCHAIN" ]] ||
    [[ -L "$NOTARY_KEYCHAIN" ]]
}; then
  printf 'DEVCONTAINER_NOTARY_KEYCHAIN must be empty or a safe absolute keychain path\n' >&2
  exit 2
fi
if [[ ! "$COMMAND_TIMEOUT_SECONDS" =~ ^[1-9][0-9]{0,4}$ ||
  ! "$NOTARY_TIMEOUT_SECONDS" =~ ^[1-9][0-9]{0,4}$ ]]; then
  printf 'signing and notarization timeouts must be positive integers\n' >&2
  exit 2
fi
if (( COMMAND_TIMEOUT_SECONDS > 3600 || NOTARY_TIMEOUT_SECONDS <= 60 ||
  NOTARY_TIMEOUT_SECONDS > 86400 )); then
  printf 'signing timeout must be at most 3600 seconds and notarization timeout must be 61 to 86400 seconds\n' >&2
  exit 2
fi
if [[ ! -d "$STAGE_DIRECTORY" ]]; then
  printf 'package stage does not exist: %s\n' "$STAGE_DIRECTORY" >&2
  exit 2
fi

for command_name in awk codesign ditto python3 shasum xcrun; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'required command is missing: %s\n' "$command_name" >&2
    exit 2
  fi
done
if [[ ! -f "$DEADLINE_RUNNER" || -L "$DEADLINE_RUNNER" ]]; then
  printf 'bounded process runner is missing or unsafe: %s\n' "$DEADLINE_RUNNER" >&2
  exit 2
fi

# Close standard input and supervise the complete child process group so a
# credential prompt or leaked helper cannot keep a release runner occupied.
run_bounded() {
  local seconds="$1"
  shift
  python3 "$DEADLINE_RUNNER" --seconds "$seconds" -- "$@" < /dev/null
}

BINARIES=(
  "$STAGE_DIRECTORY/bin/devcontainer"
  "$STAGE_DIRECTORY/bin/devcontainer-docker"
  "$STAGE_DIRECTORY/bin/devcontainer-compose"
  "$STAGE_DIRECTORY/libexec/devcontainer-compose/bin/compose"
  "$STAGE_DIRECTORY/libexec/devcontainer-compose/resources/compose-normalizer"
  "$STAGE_DIRECTORY/bin/devcontainer-engine"
  "$STAGE_DIRECTORY/libexec/container/plugins/devcontainer/bin/devcontainer"
)
for binary in "${BINARIES[@]}"; do
  if [[ ! -x "$binary" ]]; then
    printf 'package executable is missing: %s\n' "$binary" >&2
    exit 2
  fi
done

TEMPORARY_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/devcontainer-notary.XXXXXX")"
readonly TEMPORARY_DIRECTORY
readonly NOTARY_ARCHIVE="$TEMPORARY_DIRECTORY/devcontainer-notarization.zip"
readonly NOTARY_OUTPUT="$TEMPORARY_DIRECTORY/notarytool.json"

# Remove transient notarization material without touching the package stage.
# shellcheck disable=SC2329  # Invoked indirectly by the EXIT trap.
cleanup() {
  rm -rf "$TEMPORARY_DIRECTORY"
}
trap cleanup EXIT

for binary in "${BINARIES[@]}"; do
  run_bounded "$COMMAND_TIMEOUT_SECONDS" codesign \
    --force \
    --keychain "$SIGNING_KEYCHAIN" \
    --options runtime \
    --timestamp \
    --sign "$SIGNING_IDENTITY" \
    "$binary"
  run_bounded "$COMMAND_TIMEOUT_SECONDS" \
    codesign --verify --strict --verbose=2 "$binary"
done

python3 "$REPOSITORY_ROOT/Tools/release/update-sbom-file-checksum.py" \
  --sbom "$STAGE_DIRECTORY/share/devcontainer/devcontainer.spdx.json" \
  --package container-compose \
  --file "$STAGE_DIRECTORY/libexec/devcontainer-compose/bin/compose"

ditto -c -k --sequesterRsrc --keepParent "$STAGE_DIRECTORY" "$NOTARY_ARCHIVE"
NOTARY_CREDENTIALS=(--keychain-profile "$NOTARY_PROFILE")
if [[ -n "$NOTARY_KEYCHAIN" ]]; then
  NOTARY_CREDENTIALS+=(--keychain "$NOTARY_KEYCHAIN")
fi
run_bounded "$NOTARY_TIMEOUT_SECONDS" xcrun notarytool submit "$NOTARY_ARCHIVE" \
  "${NOTARY_CREDENTIALS[@]}" \
  --wait \
  --timeout "$((NOTARY_TIMEOUT_SECONDS - 60))s" \
  --output-format json >"$NOTARY_OUTPUT"

ARCHIVE_DIGEST="$(shasum -a 256 "$NOTARY_ARCHIVE" | awk '{ print $1 }')"
readonly ARCHIVE_DIGEST
python3 "$REPOSITORY_ROOT/Tools/release/write-notarization-evidence.py" \
  --input "$NOTARY_OUTPUT" \
  --archive-sha256 "$ARCHIVE_DIGEST" \
  --output "$EVIDENCE_FILE"
