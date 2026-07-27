#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.
#
# USAGE:
#   runner-preflight.sh [docker|apple-stock|apple-compose|all]

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_NAME="$(basename "$SELF_PATH")"
readonly SCRIPT_NAME
REPOSITORY_ROOT="$(cd "$(dirname "$SELF_PATH")/../.." && pwd -P)"
readonly REPOSITORY_ROOT

# Print command usage without probing a runtime.
usage() {
  sed -n 's/^# *//p' "$SELF_PATH" | sed -n '/^USAGE:/,$p'
}

# Require one executable and report a stable diagnostic.
need() {
  local command_name="$1"

  command -v "$command_name" >/dev/null || {
    printf '%s: required command is missing: %s\n' \
      "$SCRIPT_NAME" "$command_name" >&2
    return 1
  }
}

# Reject unknown lane names before any runtime probe.
validate_lane() {
  case "$1" in
    docker | apple-stock | apple-compose | all)
      ;;
    *)
      printf '%s: unsupported lane: %s\n' "$SCRIPT_NAME" "$1" >&2
      return 1
      ;;
  esac
}

# Verify the exact tools required by one isolated parity lane.
main() {
  if (( $# == 1 )) && [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    return 0
  fi
  if (( $# > 1 )); then
    usage >&2
    return 2
  fi

  local lane="${1:-all}"
  validate_lane "$lane"
  cd "$REPOSITORY_ROOT"

  if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    printf '%s: parity requires an arm64 Mac\n' "$SCRIPT_NAME" >&2
    return 1
  fi

  local command_name
  for command_name in code codesign docker jq make node npx python3 swift; do
    need "$command_name"
  done
  docker info >/dev/null
  npx --yes @devcontainers/cli@0.88.0 --version | grep -Fx '0.88.0'

  if [[ "$lane" == "docker" || "$lane" == "all" ]]; then
    need docker-compose
  fi

  local expected_code_version
  local expected_code_commit
  local actual_code_version
  expected_code_version="$(
    jq -r '.referencePins.vscode.version' Tests/Parity/manifest.json
  )"
  expected_code_commit="$(
    jq -r '.referencePins.vscode.commit' Tests/Parity/manifest.json
  )"
  actual_code_version="$(code --version)"
  grep -Fx "$expected_code_version" <<<"$actual_code_version"
  grep -Fx "$expected_code_commit" <<<"$actual_code_version"

  if [[ "$lane" != "docker" ]]; then
    need container
    container system status >/dev/null
  fi

  if [[ "$lane" == "apple-compose" || "$lane" == "all" ]]; then
    need container-compose
    container-compose version --format json \
      | jq -e '.version and .source == "stephenlclarke/container-compose"'
  fi

  printf 'trusted %s parity runner preflight passed\n' "$lane"
}

main "$@"
