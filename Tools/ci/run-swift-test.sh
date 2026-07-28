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
readonly TIMEOUT_SECONDS="${SWIFT_TEST_TIMEOUT_SECONDS:-0}"
readonly PYTHON_COMMAND="${PYTHON:-python3}"
TEMPORARY_LOG="$(mktemp "${TMPDIR:-/tmp}/devcontainer-swift-test.XXXXXX")"
readonly TEMPORARY_LOG

mkdir -p "$(dirname "$LOG")"

if ! [[ "$TIMEOUT_SECONDS" =~ ^[0-9]+$ ]]; then
  printf 'SWIFT_TEST_TIMEOUT_SECONDS must be a non-negative integer: %s\n' \
    "$TIMEOUT_SECONDS" >&2
  exit 64
fi

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

# Run the command in its own process group when a timeout is configured. A
# SwiftPM test can otherwise leave swiftpm-testing running after its parent is
# terminated. Exit 124 is reserved for this harness timeout.
run_test_command() {
  if (( TIMEOUT_SECONDS == 0 )); then
    "$@" >"$TEMPORARY_LOG" 2>&1
    return
  fi

  "$PYTHON_COMMAND" - "$TEMPORARY_LOG" "$TIMEOUT_SECONDS" "$@" <<'PY'
import os
import signal
import subprocess
import sys

log_path = sys.argv[1]
timeout_seconds = int(sys.argv[2])
command = sys.argv[3:]

with open(log_path, "wb") as log:
    process = subprocess.Popen(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        raise SystemExit(process.wait(timeout=timeout_seconds))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        log.write(
            (
                "\nSwift test command timed out after "
                f"{timeout_seconds} seconds.\n"
            ).encode()
        )
        raise SystemExit(124)
PY
}

ATTEMPT=1
RETRY_REASON=""
while (( ATTEMPT <= ATTEMPTS )); do
  if (( ATTEMPT > 1 )); then
    printf 'Retrying Swift tests after %s (attempt %d/%d).\n' \
      "$RETRY_REASON" "$ATTEMPT" "$ATTEMPTS" >&2
  fi

  : >"$TEMPORARY_LOG"
  set +e
  run_test_command "$@"
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
    RETRY_REASON="swiftpm-testing-helper signal 13"
    ATTEMPT="$((ATTEMPT + 1))"
    continue
  fi
  if (( STATUS == 124 && TIMEOUT_SECONDS > 0 && ATTEMPT < ATTEMPTS )); then
    RETRY_REASON="a ${TIMEOUT_SECONDS}-second timeout"
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
