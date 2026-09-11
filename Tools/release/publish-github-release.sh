#!/usr/bin/env bash
# USAGE:
#   publish-github-release.sh current-stage|current-finalize|stable-stage|stable-finalize
#
# Stage and publish immutable stable or commit-addressed Current assets.

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
readonly IMMUTABILITY_ATTEMPTS="${RELEASE_IMMUTABILITY_ATTEMPTS:-60}"
readonly IMMUTABILITY_DELAY_SECONDS="${RELEASE_IMMUTABILITY_DELAY_SECONDS:-10}"

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
  output="$("$GH" api "repos/$REPOSITORY/releases/tags/$TAG" 2>&1)"
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

# Require a private draft whose release metadata is still mutable.
require_release_draft() {
  local channel="$1"
  if ! jq -e \
    --arg tag "$TAG" \
    '.tag_name == $tag and .draft == true' \
    <<<"$RELEASE_DOCUMENT" >/dev/null; then
    printf '%s release %s exists but is not a private draft\n' \
      "$channel" "$TAG" >&2
    exit 1
  fi
}

# Require repository-level immutability before staging release bytes.
require_release_immutability() {
  local settings
  settings="$("$GH" api "repos/$REPOSITORY/immutable-releases")"
  if ! jq -e '.enabled == true' <<<"$settings" >/dev/null; then
    printf 'publication requires GitHub immutable releases to be enabled\n' >&2
    exit 1
  fi
}

# Resolve the remote release tag to the exact candidate commit.
verify_remote_tag_target() {
  local remote_target
  remote_target="$(
    "$GIT" ls-remote --tags "https://github.com/$REPOSITORY.git" \
      "refs/tags/$TAG" "refs/tags/$TAG^{}" |
      awk '$2 ~ /\^\{\}$/ { peeled = $1 } $2 !~ /\^\{\}$/ { direct = $1 } END { print peeled ? peeled : direct }'
  )"
  if [[ "$remote_target" != "$PUBLISH_SHA" ]]; then
    printf 'release tag target mismatch: expected %s, got %s\n' \
      "$PUBLISH_SHA" "${remote_target:-missing}" >&2
    exit 1
  fi
}

# Validate that an asset inventory has no foreign or duplicate names.
validate_release_asset_names() {
  local remote_names="$1" expected_names="$2" require_complete="$3"
  local name count
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    if ! grep -Fqx -- "$name" <<<"$expected_names"; then
      printf 'release draft contains an unexpected asset: %s\n' "$name" >&2
      return 1
    fi
    count="$(grep -Fxc -- "$name" <<<"$remote_names" || true)"
    if (( count != 1 )); then
      printf 'release draft contains a duplicate asset name: %s\n' "$name" >&2
      return 1
    fi
  done <<<"$remote_names"
  if [[ "$require_complete" == true ]]; then
    while IFS= read -r name; do
      [[ -n "$name" ]] || continue
      if ! grep -Fqx -- "$name" <<<"$remote_names"; then
        printf 'release draft is missing expected asset: %s\n' "$name" >&2
        return 1
      fi
    done <<<"$expected_names"
  fi
}

# Validate GitHub's server-computed SHA-256 digest for every release asset.
validate_release_asset_digests() {
  local remote_assets="$1" asset name count expected actual
  for asset in "${ASSETS[@]}"; do
    name="$(basename "$asset")"
    count="$(awk -F '\t' -v name="$name" \
      '$1 == name { count += 1 } END { print count + 0 }' <<<"$remote_assets")"
    if (( count != 1 )); then
      printf 'release draft final inventory changed for asset: %s\n' "$name" >&2
      return 1
    fi
    expected="sha256:$(shasum -a 256 "$asset" | awk '{print $1}')"
    actual="$(awk -F '\t' -v name="$name" '$1 == name { print $2 }' \
      <<<"$remote_assets")"
    if [[ "$actual" != "$expected" ]]; then
      printf 'release draft final digest changed for asset: %s\n' "$name" >&2
      return 1
    fi
  done
}

# Return the unique expected release asset names.
expected_release_asset_names() {
  local names="" asset name
  for asset in "${ASSETS[@]}"; do
    name="$(basename "$asset")"
    if grep -Fqx -- "$name" <<<"$names"; then
      printf 'release candidate contains a duplicate asset name: %s\n' \
        "$name" >&2
      return 1
    fi
    names+="$name"$'\n'
  done
  printf '%s' "$names"
}

# Reconcile an interrupted release draft without replacing staged bytes.
reconcile_draft_assets() {
  local temporary initial remote_names expected_names asset name
  local downloaded final_snapshot remote_assets
  local -a missing_assets=()
  expected_names="$(expected_release_asset_names)"
  remote_names="$(
    "$GH" release view "$TAG" \
      --repo "$REPOSITORY" --json assets --jq '.assets[].name'
  )"
  validate_release_asset_names "$remote_names" "$expected_names" false

  temporary="$(
    mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/devcontainer-stable-assets.XXXXXX"
  )"
  trap 'find "$temporary" -depth -delete >/dev/null 2>&1 || true' RETURN
  initial="$temporary/initial"
  mkdir -p "$initial"
  for asset in "${ASSETS[@]}"; do
    name="$(basename "$asset")"
    if grep -Fqx -- "$name" <<<"$remote_names"; then
      "$GH" release download "$TAG" \
        --repo "$REPOSITORY" --pattern "$name" --dir "$initial"
      downloaded="$initial/$name"
      if [[ ! -f "$downloaded" ]] || \
        [[ "$(shasum -a 256 "$downloaded" | awk '{print $1}')" != \
          "$(shasum -a 256 "$asset" | awk '{print $1}')" ]]; then
        printf 'release draft asset conflicts with candidate: %s\n' \
          "$name" >&2
        return 1
      fi
    else
      missing_assets+=("$asset")
    fi
  done
  if (( ${#missing_assets[@]} > 0 )); then
    "$GH" release upload "$TAG" "${missing_assets[@]}" --repo "$REPOSITORY"
  fi

  final_snapshot="$(
    "$GH" release view "$TAG" --repo "$REPOSITORY" \
      --json isDraft,assets
  )"
  if [[ "$(jq -r '.isDraft' <<<"$final_snapshot")" != true ]]; then
    printf 'release is no longer a draft before publication: %s\n' \
      "$TAG" >&2
    return 1
  fi
  remote_assets="$(
    jq -r '.assets[] | [.name, (.digest // "")] | @tsv' \
      <<<"$final_snapshot"
  )"
  remote_names="$(cut -f 1 <<<"$remote_assets")"
  validate_release_asset_names "$remote_names" "$expected_names" true
  validate_release_asset_digests "$remote_assets"
}

# Download and authenticate the exact bytes retained by an immutable release.
restore_published_assets() {
  local expected_names snapshot remote_assets remote_names temporary
  local asset name expected actual downloaded
  expected_names="$(expected_release_asset_names)"
  snapshot="$(
    "$GH" release view "$TAG" --repo "$REPOSITORY" --json assets
  )"
  remote_assets="$(
    jq -r '.assets[] | [.name, (.digest // "")] | @tsv' <<<"$snapshot"
  )"
  remote_names="$(cut -f 1 <<<"$remote_assets")"
  validate_release_asset_names "$remote_names" "$expected_names" true

  temporary="$(
    mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/devcontainer-published-assets.XXXXXX"
  )"
  trap 'find "$temporary" -depth -delete >/dev/null 2>&1 || true' RETURN
  for asset in "${ASSETS[@]}"; do
    name="$(basename "$asset")"
    expected="$(
      awk -F '\t' -v name="$name" '$1 == name { print $2 }' \
        <<<"$remote_assets"
    )"
    if [[ ! "$expected" =~ ^sha256:[0-9a-f]{64}$ ]]; then
      printf 'published asset has no valid server digest: %s\n' "$name" >&2
      return 1
    fi
    "$GH" release download "$TAG" \
      --repo "$REPOSITORY" --pattern "$name" --dir "$temporary"
    downloaded="$temporary/$name"
    if [[ ! -f "$downloaded" ]]; then
      printf 'published asset download is missing: %s\n' "$name" >&2
      return 1
    fi
    actual="sha256:$(shasum -a 256 "$downloaded" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
      printf 'published asset digest mismatch: %s\n' "$name" >&2
      return 1
    fi
  done
  for asset in "${ASSETS[@]}"; do
    name="$(basename "$asset")"
    mv "$temporary/$name" "$asset"
  done
  validate_release_asset_digests "$remote_assets"
}

# Wait for GitHub's bounded post-publication immutability transition.
wait_for_release_immutability() {
  local attempt immutable
  if [[ ! "$IMMUTABILITY_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || \
    [[ ! "$IMMUTABILITY_DELAY_SECONDS" =~ ^[0-9]+$ ]]; then
    printf 'invalid release immutability wait configuration\n' >&2
    return 1
  fi
  for ((attempt = 1; attempt <= IMMUTABILITY_ATTEMPTS; attempt += 1)); do
    immutable="$(
      "$GH" release view "$TAG" --repo "$REPOSITORY" \
        --json isImmutable --jq '.isImmutable'
    )"
    if [[ "$immutable" == true ]]; then
      return 0
    fi
    if (( attempt < IMMUTABILITY_ATTEMPTS )); then
      sleep "$IMMUTABILITY_DELAY_SECONDS"
    fi
  done
  printf 'published release did not become immutable: %s\n' "$TAG" >&2
  return 1
}

# Verify the exact immutable server state after publication or recovery.
verify_published_release() {
  local channel="$1"
  local expected_names snapshot remote_assets remote_names latest_tag field
  local actual expected expected_prerelease
  expected_prerelease=false
  if [[ "$channel" == current ]]; then
    expected_prerelease=true
  fi
  wait_for_release_immutability
  expected_names="$(expected_release_asset_names)"
  snapshot="$(
    "$GH" release view "$TAG" --repo "$REPOSITORY" \
      --json isDraft,isImmutable,isPrerelease,tagName,name,body,assets
  )"
  while IFS=$'\t' read -r field actual expected; do
    if [[ "$actual" != "$expected" ]]; then
      printf 'published %s release %s mismatch: expected %s, got %s\n' \
        "$channel" \
        "$field" "$expected" "$actual" >&2
      return 1
    fi
  done < <(
    jq -r --arg tag "$TAG" \
      --arg title "$TITLE" --rawfile body "$NOTES_FILE" \
      --argjson prerelease "$expected_prerelease" \
      '["draft state", .isDraft, false], ["immutability", .isImmutable, true], ["prerelease state", .isPrerelease, $prerelease], ["tag", .tagName, $tag], ["title", .name, $title], ["notes", .body, $body] | @tsv' \
      <<<"$snapshot"
  )
  remote_assets="$(
    jq -r '.assets[] | [.name, (.digest // "")] | @tsv' <<<"$snapshot"
  )"
  remote_names="$(cut -f 1 <<<"$remote_assets")"
  validate_release_asset_names "$remote_names" "$expected_names" true
  validate_release_asset_digests "$remote_assets"
  if [[ "$channel" == stable ]]; then
    latest_tag="$(
      "$GH" api "repos/$REPOSITORY/releases/latest" --jq '.tag_name'
    )"
    if [[ "$latest_tag" != "$TAG" ]]; then
      printf 'published stable release is not latest: expected %s, got %s\n' \
        "$TAG" "${latest_tag:-missing}" >&2
      return 1
    fi
  fi
  verify_remote_tag_target
}

case "$MODE" in
  current-stage)
    if [[ ! "$TAG" =~ ^current-[0-9a-f]{40}$ ]] || \
      [[ "$TAG" != "current-$PUBLISH_SHA" ]]; then
      printf 'Current publication must use its exact commit-addressed tag\n' >&2
      exit 2
    fi
    require_release_immutability
    if release_exists; then
      if [[ "$(jq -r '.draft' <<<"$RELEASE_DOCUMENT")" != true ]]; then
        restore_published_assets
        verify_published_release current
        exit 0
      fi
      require_release_draft current
      reconcile_draft_assets
      "$GH" release edit "$TAG" \
        --repo "$REPOSITORY" \
        --target "$PUBLISH_SHA" \
        --title "$TITLE" \
        --notes-file "$NOTES_FILE" \
        --prerelease \
        --latest=false
    else
      "$GH" release create "$TAG" "${ASSETS[@]}" \
        --repo "$REPOSITORY" \
        --target "$PUBLISH_SHA" \
        --title "$TITLE" \
        --notes-file "$NOTES_FILE" \
        --draft \
        --prerelease \
        --latest=false
      if ! release_exists; then
        printf 'Current draft was not visible after creation: %s\n' "$TAG" >&2
        exit 1
      fi
      require_release_draft current
      reconcile_draft_assets
    fi
    ;;
  current-finalize)
    if [[ ! "$TAG" =~ ^current-[0-9a-f]{40}$ ]] || \
      [[ "$TAG" != "current-$PUBLISH_SHA" ]]; then
      printf 'Current publication must use its exact commit-addressed tag\n' >&2
      exit 2
    fi
    require_release_immutability
    if ! release_exists; then
      printf 'Current release must be staged before finalization\n' >&2
      exit 1
    fi
    if [[ "$(jq -r '.draft' <<<"$RELEASE_DOCUMENT")" != true ]]; then
      restore_published_assets
      verify_published_release current
      exit 0
    fi
    require_release_draft current
    reconcile_draft_assets
    "$GH" release edit "$TAG" \
      --repo "$REPOSITORY" \
      --target "$PUBLISH_SHA" \
      --title "$TITLE" \
      --notes-file "$NOTES_FILE" \
      --draft=false \
      --prerelease \
      --latest=false
    verify_published_release current
    ;;
  stable-stage)
    if [[ ! "$TAG" =~ ^[0-9]+[.][0-9]+[.][0-9]+$ ]]; then
      printf 'stable release tag must be MAJOR.MINOR.PATCH\n' >&2
      exit 2
    fi
    require_release_immutability
    verify_remote_tag_target
    if release_exists; then
      if [[ "$(jq -r '.draft' <<<"$RELEASE_DOCUMENT")" != true ]]; then
        restore_published_assets
        verify_published_release stable
        exit 0
      fi
      require_release_draft stable
      reconcile_draft_assets
      "$GH" release edit "$TAG" \
        --repo "$REPOSITORY" \
        --target "$PUBLISH_SHA" \
        --title "$TITLE" \
        --notes-file "$NOTES_FILE" \
        --latest=false
    else
      "$GH" release create "$TAG" "${ASSETS[@]}" \
        --repo "$REPOSITORY" \
        --target "$PUBLISH_SHA" \
        --verify-tag \
        --title "$TITLE" \
        --notes-file "$NOTES_FILE" \
        --draft \
        --latest=false
      if ! release_exists; then
        printf 'stable draft was not visible after creation: %s\n' "$TAG" >&2
        exit 1
      fi
      require_release_draft stable
      reconcile_draft_assets
    fi
    ;;
  stable-finalize)
    if [[ ! "$TAG" =~ ^[0-9]+[.][0-9]+[.][0-9]+$ ]]; then
      printf 'stable release tag must be MAJOR.MINOR.PATCH\n' >&2
      exit 2
    fi
    require_release_immutability
    verify_remote_tag_target
    if ! release_exists; then
      printf 'stable release must be staged before finalization\n' >&2
      exit 1
    fi
    if [[ "$(jq -r '.draft' <<<"$RELEASE_DOCUMENT")" != true ]]; then
      restore_published_assets
      verify_published_release stable
      exit 0
    fi
    require_release_draft stable
    reconcile_draft_assets
    "$GH" release edit "$TAG" \
      --repo "$REPOSITORY" \
      --target "$PUBLISH_SHA" \
      --title "$TITLE" \
      --notes-file "$NOTES_FILE" \
      --draft=false \
      --prerelease=false \
      --latest
    verify_published_release stable
    ;;
  *)
    printf 'unsupported publication mode: %s\n' "$MODE" >&2
    usage >&2
    exit 2
    ;;
esac
