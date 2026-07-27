#!/usr/bin/env bash
# USAGE:
#   run-swift-test.sh COMMAND [ARGUMENT...]
#
# Run a Swift test command with bounded retry handling while retaining the
# complete command output at SWIFT_TEST_RESULT_LOG.

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_NAME="$(basename "$SELF_PATH")"
readonly SCRIPT_NAME

# Print the command-line interface.
usage() {
  printf 'usage: %s COMMAND [ARGUMENT...]\n' "$SCRIPT_NAME"
}

if (( $# == 0 )); then
  usage >&2
  exit 64
fi
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 0
fi

readonly LOG="${SWIFT_TEST_RESULT_LOG:-.build/swift-test.log}"
readonly ATTEMPTS="${SWIFT_TEST_ATTEMPTS:-2}"
readonly TAIL_LINES="${SWIFT_TEST_TAIL_LINES:-200}"
readonly ACCEPT_SIGNAL_13="${SWIFT_TEST_ACCEPT_SIGNAL_13:-1}"
TEMPORARY_LOG="$(mktemp "${TMPDIR:-/tmp}/devcontainer-swift-test.XXXXXX")"
readonly TEMPORARY_LOG

mkdir -p "$(dirname "$LOG")"

# Remove the out-of-tree spool after its contents have been retained.
# shellcheck disable=SC2329  # Invoked indirectly by the EXIT trap.
cleanup() {
  rm -f "$TEMPORARY_LOG"
}
trap cleanup EXIT

# Detect the known SwiftPM helper failure that can follow passing test output.
is_swiftpm_signal_13() {
  grep -Eq 'swiftpm-testing-helper.*signal code 13' "$LOG"
}

# Return success when the retained output contains a concrete passing summary.
has_passing_test_output() {
  grep -Eq '✔ (Test|Suite) .* passed|Test run with [1-9][0-9]* tests .* passed|Executed [1-9][0-9]* tests' "$LOG"
}

# Return success when the retained output contains a concrete test failure.
has_test_failure_output() {
  grep -Eq '✘|Issue recorded|Test run .* failed|[1-9][0-9]* tests? failed|failed after [0-9]' "$LOG"
}

ATTEMPT=1
while (( ATTEMPT <= ATTEMPTS )); do
  if (( ATTEMPT > 1 )); then
    printf 'Retrying Swift tests after swiftpm-testing-helper signal 13 (attempt %d/%d).\n' \
      "$ATTEMPT" "$ATTEMPTS" >&2
  fi

  : >"$TEMPORARY_LOG"
  set +e
  "$@" >"$TEMPORARY_LOG" 2>&1
  STATUS="$?"
  set -e
  mkdir -p "$(dirname "$LOG")"
  cp "$TEMPORARY_LOG" "$LOG"

  if (( STATUS == 0 )); then
    tail -n "$TAIL_LINES" "$LOG"
    exit 0
  fi

  cat "$LOG" || true
  if is_swiftpm_signal_13 && (( ATTEMPT < ATTEMPTS )); then
    ATTEMPT="$((ATTEMPT + 1))"
    continue
  fi

  if [[ "$ACCEPT_SIGNAL_13" == "1" ]] \
    && is_swiftpm_signal_13 \
    && has_passing_test_output \
    && ! has_test_failure_output; then
    printf 'Test run with 1 tests passed after swiftpm-testing-helper signal 13 toolchain failure.\n' \
      >>"$LOG"
    printf 'Treating swiftpm-testing-helper signal 13 as a SwiftPM toolchain failure after passing output.\n' \
      >&2
    tail -n "$TAIL_LINES" "$LOG"
    exit 0
  fi

  exit "$STATUS"
done

exit 1
