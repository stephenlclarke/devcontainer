#!/usr/bin/env bash
# USAGE:
#   run-swift-testing-bundle.sh BUNDLE_EXECUTABLE [TESTING_ARGUMENT...]
#
# Load a prebuilt Swift Testing bundle without asking SwiftPM to plan or launch
# the already-built test product a second time.

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_NAME="$(basename "$SELF_PATH")"
readonly SCRIPT_NAME

usage() {
  printf 'usage: %s BUNDLE_EXECUTABLE [TESTING_ARGUMENT...]\n' "$SCRIPT_NAME"
}

if (( $# == 0 )); then
  usage >&2
  exit 64
fi
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 0
fi

readonly BUNDLE_EXECUTABLE="$1"
shift

SANITIZER_KIND=""
for argument in "$@"; do
  case "$argument" in
    --sanitize=address)
      if [[ -n "$SANITIZER_KIND" && "$SANITIZER_KIND" != "asan" ]]; then
        printf 'Only one Swift sanitizer can be active per test run.\n' >&2
        exit 64
      fi
      SANITIZER_KIND="asan"
      ;;
    --sanitize=thread)
      if [[ -n "$SANITIZER_KIND" && "$SANITIZER_KIND" != "tsan" ]]; then
        printf 'Only one Swift sanitizer can be active per test run.\n' >&2
        exit 64
      fi
      SANITIZER_KIND="tsan"
      ;;
  esac
done
readonly SANITIZER_KIND

if [[ "$BUNDLE_EXECUTABLE" != /* ]]; then
  printf 'Swift test bundle executable must be absolute: %s\n' \
    "$BUNDLE_EXECUTABLE" >&2
  exit 64
fi
if [[ ! -f "$BUNDLE_EXECUTABLE" ]]; then
  printf 'Swift test bundle executable does not exist: %s\n' \
    "$BUNDLE_EXECUTABLE" >&2
  exit 66
fi

HELPER="${SWIFT_TEST_HELPER:-}"
if [[ -z "$HELPER" ]]; then
  SWIFTC="${SWIFT_TEST_SWIFTC:-$(xcrun --find swiftc)}"
  readonly SWIFTC
  if [[ "$SWIFTC" != */bin/swiftc ]]; then
    printf 'Cannot derive SwiftPM helper from swiftc path: %s\n' "$SWIFTC" >&2
    exit 69
  fi
  HELPER="${SWIFTC%/bin/swiftc}/libexec/swift/pm/swiftpm-testing-helper"
fi
readonly HELPER

if [[ ! -x "$HELPER" ]]; then
  printf 'SwiftPM testing helper is not executable: %s\n' "$HELPER" >&2
  exit 69
fi

PLATFORM_PATH="${SWIFT_TEST_PLATFORM_PATH:-}"
if [[ -z "$PLATFORM_PATH" ]]; then
  PLATFORM_PATH="$(xcrun --sdk macosx --show-sdk-platform-path)"
fi
readonly PLATFORM_PATH

if [[ ! -d "$PLATFORM_PATH/Developer/Library/Frameworks" ]]; then
  printf 'macOS platform developer frameworks do not exist: %s\n' \
    "$PLATFORM_PATH" >&2
  exit 69
fi

readonly FRAMEWORK_PATH="$PLATFORM_PATH/Developer/Library/Frameworks:$PLATFORM_PATH/Developer/Library/PrivateFrameworks"
readonly LIBRARY_PATH="$PLATFORM_PATH/Developer/usr/lib"
export DYLD_FRAMEWORK_PATH="$FRAMEWORK_PATH${DYLD_FRAMEWORK_PATH:+:$DYLD_FRAMEWORK_PATH}"
export DYLD_LIBRARY_PATH="$LIBRARY_PATH${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"

if [[ -n "$SANITIZER_KIND" ]]; then
  CLANG="${SWIFT_TEST_CLANG:-$(xcrun --find clang)}"
  readonly CLANG
  SANITIZER_RUNTIME="$("$CLANG" \
    -print-file-name="libclang_rt.${SANITIZER_KIND}_osx_dynamic.dylib")"
  readonly SANITIZER_RUNTIME
  if [[ ! -f "$SANITIZER_RUNTIME" ]]; then
    printf 'Swift %s sanitizer runtime does not exist: %s\n' \
      "$SANITIZER_KIND" "$SANITIZER_RUNTIME" >&2
    exit 69
  fi
  export DYLD_INSERT_LIBRARIES="$SANITIZER_RUNTIME${DYLD_INSERT_LIBRARIES:+:$DYLD_INSERT_LIBRARIES}"
fi

exec "$HELPER" \
  --test-bundle-path "$BUNDLE_EXECUTABLE" \
  "$@" \
  --testing-library swift-testing
