#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: coverage-counters.sh PROBE EXPECTED_COUNTERS
# Verify native parallel instrumentation before trusting product coverage.
set -euo pipefail

# Print the narrow Bazel-only interface without executing a probe.
usage() {
    printf 'Usage: %s PROBE EXPECTED_COUNTERS (Bazel coverage only)\n' "${0##*/}"
}

# Compare every raw function/line counter, including unexecuted branches. The
# reviewed inventory deliberately changes if the fixture or compiler changes.
validate_counters() {
    /usr/bin/awk '/^(FNDA|DA|LF|LH):/' "$1" | LC_ALL=C /usr/bin/sort > "$TEST_TMPDIR/actual-counters"
    /usr/bin/diff -u "$2" "$TEST_TMPDIR/actual-counters"
}

# The validator must reject missing records and plausible in-range lost hits,
# not just the unsigned-underflow symptom originally seen in product coverage.
reject_mutations() {
    local report="$1" expected="$2" mutation
    for mutation in '/^DA:9,/d' 's/^DA:9,320000$/DA:9,319999/' 's/^DA:9,320000$/DA:9,18446744073709551615/' '/^FNDA:/d'; do
        /usr/bin/sed "$mutation" "$report" > "$TEST_TMPDIR/mutated-counters.lcov"
        if validate_counters "$TEST_TMPDIR/mutated-counters.lcov" "$expected" > "$TEST_TMPDIR/mutation-diff"; then
            printf 'Counter validator accepted mutation: %s\n' "$mutation" >&2
            return 1
        fi
    done
}

# Collect one exact invocation and reject lost, wrapped or absent counters.
main() {
    if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then usage; return; fi
    [[ $# == 2 && "${COVERAGE:-0}" == 1 ]] || { usage >&2; return 2; }
    [[ "${TEST_TMPDIR:-}" == /Volumes/SSD/cf/bazel/* && "${COVERAGE_DIR:-}" == /Volumes/SSD/cf/bazel/* ]] || return 2
    local probe="$1" expected="$2" profile="$COVERAGE_DIR/counter-probe.profraw"
    [[ ! -e "$profile" ]] || { printf 'Unexpected pre-existing profile.\n' >&2; return 2; }
    LLVM_PROFILE_FILE="$profile" "$probe"
    /usr/bin/xcrun llvm-profdata merge -sparse "$profile" -o "$TEST_TMPDIR/counter-probe.profdata"
    /usr/bin/xcrun llvm-cov export -format=lcov \
        -instr-profile="$TEST_TMPDIR/counter-probe.profdata" "$probe" > "$TEST_TMPDIR/counter-probe.lcov"
    # Print raw evidence before validation so a failed test remains diagnosable.
    /bin/cat "$TEST_TMPDIR/counter-probe.lcov"
    validate_counters "$TEST_TMPDIR/counter-probe.lcov" "$expected"
    reject_mutations "$TEST_TMPDIR/counter-probe.lcov" "$expected"
}

main "$@"
