#!/usr/bin/env bash
#
# Run the pinned GitHub artifact action without runner-managed action download.

set -euo pipefail

if (( $# != 2 )); then
  printf 'usage: %s <artifact-name> <artifact-paths>\n' "$0" >&2
  exit 64
fi

artifact_name=$1
artifact_paths=$2
action_revision=043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
archive_sha256=d14fb1cada435a236a66b448fbb370cd126564c2c2d6cb52abd14d20bcbb9748
temporary_directory=$(mktemp -d "${TMPDIR:-/tmp}/devcontainer-upload-artifact.XXXXXX")
archive_path="${temporary_directory}/action.tar.gz"
action_directory="${temporary_directory}/action"

cleanup() {
  rm -rf -- "${temporary_directory}"
}
trap cleanup EXIT

mkdir -p "${action_directory}"
curl \
  --fail \
  --location \
  --retry 5 \
  --retry-all-errors \
  --silent \
  --show-error \
  "https://codeload.github.com/actions/upload-artifact/tar.gz/${action_revision}" \
  --output "${archive_path}"

actual_sha256=$(shasum -a 256 "${archive_path}" | awk '{ print $1 }')
if [[ "${actual_sha256}" != "${archive_sha256}" ]]; then
  printf 'upload-artifact archive digest mismatch: expected %s, got %s\n' \
    "${archive_sha256}" "${actual_sha256}" >&2
  exit 1
fi

tar -xzf "${archive_path}" --strip-components=1 -C "${action_directory}"
test -f "${action_directory}/dist/upload/index.js"

env \
  "INPUT_NAME=${artifact_name}" \
  "INPUT_PATH=${artifact_paths}" \
  "INPUT_IF-NO-FILES-FOUND=error" \
  "INPUT_RETENTION-DAYS=" \
  "INPUT_COMPRESSION-LEVEL=6" \
  "INPUT_OVERWRITE=false" \
  "INPUT_INCLUDE-HIDDEN-FILES=true" \
  "INPUT_ARCHIVE=true" \
  node "${action_directory}/dist/upload/index.js"
