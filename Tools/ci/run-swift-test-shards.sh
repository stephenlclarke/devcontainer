#!/usr/bin/env bash
# USAGE:
#   run-swift-test-shards.sh TEST_RUNNER BUNDLE_RUNNER BUNDLE_EXECUTABLE [TESTING_ARGUMENT...]
#
# Run every discovered Swift test target in a fresh process. Sanitizer builds
# can otherwise accumulate enough allocator quarantine state across the full
# package test bundle for macOS to terminate the runner before the suite ends.

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_NAME="$(basename "$SELF_PATH")"
readonly SCRIPT_NAME

usage() {
  printf 'usage: %s TEST_RUNNER BUNDLE_RUNNER BUNDLE_EXECUTABLE [TESTING_ARGUMENT...]\n' \
    "$SCRIPT_NAME"
}

if (( $# < 3 )); then
  usage >&2
  exit 64
fi

readonly TEST_RUNNER="$1"
readonly BUNDLE_RUNNER="$2"
readonly BUNDLE_EXECUTABLE="$3"
shift 3
readonly TESTING_ARGUMENTS=("$@")
readonly AGGREGATE_LOG="${SWIFT_TEST_RESULT_LOG:-.build/swift-test.log}"

TEMPORARY_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/devcontainer-swift-shards.XXXXXX")"
readonly TEMPORARY_ROOT
readonly TEST_LIST="$TEMPORARY_ROOT/tests.txt"

cleanup() {
  rm -rf "$TEMPORARY_ROOT"
}
trap cleanup EXIT

if ! "$BUNDLE_RUNNER" "$BUNDLE_EXECUTABLE" \
  "${TESTING_ARGUMENTS[@]}" --list-tests >"$TEST_LIST"; then
  printf 'Could not discover Swift test targets.\n' >&2
  exit 1
fi

TARGETS=()
while IFS= read -r target; do
  [[ -n "$target" ]] && TARGETS+=("$target")
done < <(
  awk -F. '/^[[:alnum:]_]+\./ { print $1 }' "$TEST_LIST" | sort -u
)
readonly TARGETS

if (( ${#TARGETS[@]} == 0 )); then
  printf 'Swift test discovery returned no test targets.\n' >&2
  exit 1
fi

mkdir -p "$(dirname "$AGGREGATE_LOG")"
: >"$AGGREGATE_LOG"
shard_index=0

for target in "${TARGETS[@]}"; do
  shard_log="$TEMPORARY_ROOT/${target}.log"
  printf 'Running Swift test target %s (%s/%s).\n' \
    "$target" "$((++shard_index))" "${#TARGETS[@]}"
  set +e
  SWIFT_TEST_RESULT_LOG="$shard_log" \
    "$TEST_RUNNER" "$BUNDLE_RUNNER" "$BUNDLE_EXECUTABLE" \
      "${TESTING_ARGUMENTS[@]}" --filter "^${target}\\."
  status="$?"
  set -e
  if [[ -f "$shard_log" ]]; then
    {
      printf '\n===== %s =====\n' "$target"
      cat "$shard_log"
    } >>"$AGGREGATE_LOG"
  fi
  if (( status != 0 )); then
    exit "$status"
  fi
done
