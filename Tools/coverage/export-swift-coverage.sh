#!/usr/bin/env bash
# USAGE:
#   export-swift-coverage.sh SWIFT_BIN_DIRECTORY
#
# Merge test and instrumented CLI profiles, then export one LLVM JSON report.

set -euo pipefail

if (( $# != 1 )); then
  printf 'usage: %s SWIFT_BIN_DIRECTORY\n' "$(basename "$0")" >&2
  exit 64
fi

readonly BIN_DIRECTORY="$1"
readonly PROFILE_DIRECTORY="$BIN_DIRECTORY/codecov"
readonly PROFILE_DATA="$PROFILE_DIRECTORY/default.profdata"
readonly OUTPUT="$PROFILE_DIRECTORY/devcontainer.json"
readonly DEVCONTAINER="$BIN_DIRECTORY/devcontainer"
readonly DEVCONTAINER_COMPOSE="$BIN_DIRECTORY/devcontainer-compose"
readonly LLVM_PROFDATA="${SWIFT_LLVM_PROFDATA:-$(xcrun --find llvm-profdata)}"
readonly LLVM_COV="${SWIFT_LLVM_COV:-$(xcrun --find llvm-cov)}"

shopt -s nullglob
RAW_PROFILES=("$PROFILE_DIRECTORY"/*.profraw)
TEST_BINARIES=("$BIN_DIRECTORY"/*.xctest/Contents/MacOS/*PackageTests)
shopt -u nullglob

if (( ${#RAW_PROFILES[@]} == 0 )); then
  printf 'no Swift coverage profiles found in %s\n' "$PROFILE_DIRECTORY" >&2
  exit 2
fi
if (( ${#TEST_BINARIES[@]} != 1 )); then
  printf 'expected one Swift package test binary, found %d\n' \
    "${#TEST_BINARIES[@]}" >&2
  exit 2
fi
for executable in "$DEVCONTAINER" "$DEVCONTAINER_COMPOSE"; do
  if [[ ! -x "$executable" ]]; then
    printf 'instrumented executable is missing: %s\n' "$executable" >&2
    exit 2
  fi
done

"$LLVM_PROFDATA" merge -sparse "${RAW_PROFILES[@]}" -o "$PROFILE_DATA"
"$LLVM_COV" export \
  -instr-profile "$PROFILE_DATA" \
  "${TEST_BINARIES[0]}" \
  -object "$DEVCONTAINER" \
  -object "$DEVCONTAINER_COMPOSE" \
  >"$OUTPUT"

printf '%s\n' "$OUTPUT"
