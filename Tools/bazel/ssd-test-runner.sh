#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: ssd-test-runner.sh TEST_EXECUTABLE [ARGS...]
# Give tools a writable per-test TMPDIR. Foundation on Darwin can still prefer
# its system directory, so Swift fixtures must explicitly honour TEST_TMPDIR.
# DEVCONTAINER_TEST_STANDALONE=1 runs a byte-identical copy outside rules_swift's
# incomplete .xctest bundle when a test authenticates its own code identity.
set -euo pipefail

[[ "${TEST_TMPDIR:-}" == /Volumes/SSD/cf/bazel/* && -d "$TEST_TMPDIR" && -w "$TEST_TMPDIR" ]] || {
    printf 'Bazel test requires its own writable SSD temporary directory.\n' >&2
    exit 2
}
TEST_TMPDIR="$(cd "$TEST_TMPDIR" && pwd -P)"
[[ "$TEST_TMPDIR" == /Volumes/SSD/cf/bazel/* && "$(cd /Volumes/SSD/cf/bazel && pwd -P)" == /Volumes/SSD/cf/bazel ]] || {
    printf 'Bazel test scratch resolves outside its enrolled storage.\n' >&2
    exit 2
}
export TEST_TMPDIR
export BAZEL_TEST=1
export TMPDIR="$TEST_TMPDIR" TMP="$TEST_TMPDIR" TEMP="$TEST_TMPDIR"
export DEVCONTAINER_TEST_SCRATCH_ROOT=/Volumes/SSD/cf/bazel/
case "${DEVCONTAINER_TEST_STANDALONE:-0}" in
    0) : ;;
    1)
        [[ $# -gt 0 && -f "$1" && -x "$1" ]] || exit 2
        standalone_executable="$(/usr/bin/mktemp "$TEST_TMPDIR/provider-test.XXXXXX")"
        trap '/bin/rm -f -- "$standalone_executable"' EXIT
        trap 'exit 129' HUP
        trap 'exit 130' INT
        trap 'exit 143' TERM
        /bin/cp "$1" "$standalone_executable"
        /bin/chmod 700 "$standalone_executable"
        /usr/bin/codesign --verify --strict "$standalone_executable"
        set -- "$standalone_executable" "${@:2}"
        ;;
    *) printf 'Standalone test mode must be 0 or 1.\n' >&2; exit 2 ;;
esac
if "$@"; then
    status=0
else
    status=$?
fi
[[ "$status" == 0 ]] || exit "$status"

# rules_swift instruments Swift but this pinned Bazel tuple does not invoke a
# native LLVM collector. Export the profiles from this execution into the
# standard COVERAGE_DIR; Bazel's existing LCOV merger handles source filtering.
if [[ "${COVERAGE:-0}" == 1 && -n "${TEST_BINARIES_FOR_LLVM_COV:-}" ]]; then
    [[ "${COVERAGE_DIR:-}" == /Volumes/SSD/cf/bazel/* ]] || exit 2
    shopt -s nullglob
    profiles=("$COVERAGE_DIR"/*.profraw)
    if [[ ${#profiles[@]} == 0 ]]; then
        # A framework-only probe has no instrumented production sources.
        [[ ! -s "${COVERAGE_MANIFEST:?}" ]] && exit 0
        printf 'Coverage execution produced no LLVM profiles (binary: %s).\n' "$TEST_BINARIES_FOR_LLVM_COV" >&2
        exit 2
    else
        # The LCOV merger rejects mixed .profdata and .dat input. Keep the
        # intermediate profile in test scratch, outside its discovery tree.
        /usr/bin/xcrun llvm-profdata merge -sparse "${profiles[@]}" -o "$TEST_TMPDIR/swift.profdata"
        /usr/bin/xcrun llvm-cov export -format=lcov \
            -instr-profile="$TEST_TMPDIR/swift.profdata" \
            "$TEST_BINARIES_FOR_LLVM_COV" > "$COVERAGE_DIR/swift.dat"
    fi
fi
