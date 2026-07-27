#!/usr/bin/env bash
# USAGE:
#   publish-github-release.sh current-stage|current-finalize|stable-stage|stable-finalize
#
# Stage/finalize immutable stable assets or the mutable Current channel.

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
readonly GH="${GH:-gh}"
readonly GIT="${GIT:-git}"

# Print the command-line interface.
usage() {
  printf \
    'usage: %s current-stage|current-finalize|stable-stage|stable-finalize\n' \
    "$SCRIPT_NAME"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if (( $# != 1 )); then
  usage >&2
  exit 2
fi

readonly MODE="$1"
readonly REPOSITORY="${RELEASE_REPOSITORY:-}"
readonly TAG="${RELEASE_TAG:-}"
readonly PUBLISH_SHA="${PUBLISH_SHA:-}"
readonly TITLE="${RELEASE_TITLE:-}"
readonly NOTES_FILE="${RELEASE_NOTES_FILE:-}"
readonly ASSETS_FILE="${RELEASE_ASSETS_FILE:-}"
RELEASE_DOCUMENT=""

for variable_name in REPOSITORY TAG PUBLISH_SHA TITLE NOTES_FILE ASSETS_FILE; do
  if [[ -z "${!variable_name}" ]]; then
    printf 'required release variable is empty: %s\n' "$variable_name" >&2
    exit 2
  fi
done
if [[ ! "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  printf 'invalid release repository: %s\n' "$REPOSITORY" >&2
  exit 2
fi
if [[ ! "$PUBLISH_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'publish SHA must be 40 lowercase hexadecimal characters\n' >&2
  exit 2
fi
if [[ ! -f "$NOTES_FILE" || ! -f "$ASSETS_FILE" ]]; then
  printf 'release notes or asset manifest is missing\n' >&2
  exit 2
fi

ASSETS=()
while IFS= read -r asset; do
  [[ -n "$asset" ]] || continue
  if [[ ! -f "$asset" ]]; then
    printf 'release asset is missing: %s\n' "$asset" >&2
    exit 2
  fi
  ASSETS+=("$asset")
done <"$ASSETS_FILE"
if (( ${#ASSETS[@]} == 0 )); then
  printf 'release asset manifest is empty\n' >&2
  exit 2
fi

# Report whether a release exists while preserving non-404 API failures.
release_exists() {
  local output status
  set +e
  output="$("$GH" api --silent "repos/$REPOSITORY/releases/tags/$TAG" 2>&1)"
  status="$?"
  set -e
  if (( status == 0 )); then
    RELEASE_DOCUMENT="$output"
    return 0
  fi
  if [[ "$output" == *"HTTP 404"* ]]; then
    return 1
  fi
  printf 'could not determine release state for %s:\n%s\n' "$TAG" "$output" >&2
  exit "$status"
}

# Require an existing public prerelease for the selected channel.
require_staged_release() {
  if ! jq -e \
    --arg tag "$TAG" \
    '
      .tag_name == $tag and
      .draft == false and
      .prerelease == true
    ' <<<"$RELEASE_DOCUMENT" >/dev/null; then
    printf 'release %s exists but is not a staged prerelease\n' "$TAG" >&2
    exit 1
  fi
}

# Move the deliberately mutable Current source pointer.
move_current_tag() {
  "$GIT" tag --no-sign --force current "$PUBLISH_SHA"
  "$GIT" push --force origin refs/tags/current
}

case "$MODE" in
  current-stage)
    if [[ "$TAG" != "current" ]]; then
      printf 'Current publication must use the current tag\n' >&2
      exit 2
    fi
    if release_exists; then
      require_staged_release
      "$GH" release upload "$TAG" "${ASSETS[@]}" \
        --repo "$REPOSITORY" \
        --clobber
    else
      move_current_tag
      "$GH" release create "$TAG" "${ASSETS[@]}" \
        --repo "$REPOSITORY" \
        --target "$PUBLISH_SHA" \
        --title "$TITLE" \
        --notes-file "$NOTES_FILE" \
        --prerelease \
        --latest=false
    fi
    ;;
  current-finalize)
    if [[ "$TAG" != "current" ]]; then
      printf 'Current publication must use the current tag\n' >&2
      exit 2
    fi
    if ! release_exists; then
      printf 'Current release must be staged before finalization\n' >&2
      exit 1
    fi
    require_staged_release
    move_current_tag
    "$GH" release edit "$TAG" \
      --repo "$REPOSITORY" \
      --target "$PUBLISH_SHA" \
      --title "$TITLE" \
      --notes-file "$NOTES_FILE" \
      --prerelease \
      --latest=false
    ;;
  stable-stage)
    if [[ ! "$TAG" =~ ^[0-9]+[.][0-9]+[.][0-9]+$ ]]; then
      printf 'stable release tag must be MAJOR.MINOR.PATCH\n' >&2
      exit 2
    fi
    if release_exists; then
      require_staged_release
      "$GH" release upload "$TAG" "${ASSETS[@]}" \
        --repo "$REPOSITORY" \
        --clobber
    else
      "$GH" release create "$TAG" "${ASSETS[@]}" \
        --repo "$REPOSITORY" \
        --verify-tag \
        --title "$TITLE" \
        --notes-file "$NOTES_FILE" \
        --prerelease \
        --latest=false
    fi
    ;;
  stable-finalize)
    if [[ ! "$TAG" =~ ^[0-9]+[.][0-9]+[.][0-9]+$ ]]; then
      printf 'stable release tag must be MAJOR.MINOR.PATCH\n' >&2
      exit 2
    fi
    if ! release_exists; then
      printf 'stable release must be staged before finalization\n' >&2
      exit 1
    fi
    require_staged_release
    "$GH" release edit "$TAG" \
      --repo "$REPOSITORY" \
      --title "$TITLE" \
      --notes-file "$NOTES_FILE" \
      --prerelease=false \
      --latest
    ;;
  *)
    printf 'unsupported publication mode: %s\n' "$MODE" >&2
    usage >&2
    exit 2
    ;;
esac
