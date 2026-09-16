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
readonly DEVCONTAINER_DOCKER="$BIN_DIRECTORY/devcontainer-docker"
readonly LLVM_PROFDATA="${SWIFT_LLVM_PROFDATA:-$(xcrun --find llvm-profdata)}"
readonly LLVM_COV="${SWIFT_LLVM_COV:-$(xcrun --find llvm-cov)}"

shopt -s nullglob
RAW_PROFILES=("$PROFILE_DIRECTORY"/*.profraw)
TEST_BINARIES=("$BIN_DIRECTORY"/*.xctest/Contents/MacOS/*PackageTests)
if (( ${#TEST_BINARIES[@]} == 0 )); then
  TEST_BINARIES=("$BIN_DIRECTORY"/*Tests.xctest/Contents/MacOS/*Tests)
fi
shopt -u nullglob

if (( ${#RAW_PROFILES[@]} == 0 )); then
  printf 'no Swift coverage profiles found in %s\n' "$PROFILE_DIRECTORY" >&2
  exit 2
fi
if (( ${#TEST_BINARIES[@]} == 0 )); then
  printf 'expected at least one Swift test binary, found %d\n' \
    "${#TEST_BINARIES[@]}" >&2
  exit 2
fi
for executable in "$DEVCONTAINER" "$DEVCONTAINER_COMPOSE" "$DEVCONTAINER_DOCKER"; do
  if [[ ! -x "$executable" ]]; then
    printf 'instrumented executable is missing: %s\n' "$executable" >&2
    exit 2
  fi
done

"$LLVM_PROFDATA" merge -sparse "${RAW_PROFILES[@]}" -o "$PROFILE_DATA"
LLVM_COV_COMMAND=(
  "$LLVM_COV" export
  -instr-profile "$PROFILE_DATA"
  "${TEST_BINARIES[0]}"
)
for test_binary in "${TEST_BINARIES[@]:1}"; do
  LLVM_COV_COMMAND+=( -object "$test_binary" )
done
LLVM_COV_COMMAND+=(
  -object "$DEVCONTAINER"
  -object "$DEVCONTAINER_COMPOSE"
  -object "$DEVCONTAINER_DOCKER"
)
"${LLVM_COV_COMMAND[@]}" >"$OUTPUT"

printf '%s\n' "$OUTPUT"
