#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.
#
# USAGE:
#   run-vscode.sh EVIDENCE_DIR [LANE...]
#
# Lanes default to docker, apple-stock, and container-compose. Set
# DEVCONTAINER_VSCODE_LANES to a space-separated subset when a dedicated
# runner owns only one runtime distribution.

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_NAME="$(basename "$SELF_PATH")"
readonly SCRIPT_NAME
REPOSITORY_ROOT="$(cd "$(dirname "$SELF_PATH")/../.." && pwd -P)"
readonly REPOSITORY_ROOT
readonly ALL_LANES=(docker apple-stock container-compose)

# Print command usage without starting a VS Code session.
usage() {
  sed -n 's/^# *//p' "$SELF_PATH" | sed -n '/^USAGE:/,$p'
}

# Reject lane spelling mistakes before any runtime side effects.
validate_lane() {
  local requested="$1"
  local lane

  for lane in "${ALL_LANES[@]}"; do
    if [[ "$lane" == "$requested" ]]; then
      return 0
    fi
  done

  printf '%s: unsupported lane: %s\n' "$SCRIPT_NAME" "$requested" >&2
  return 1
}

# Run the authenticated VS Code driver for each explicitly selected lane.
main() {
  local argument_count="$#"
  local first_argument="${1:-}"

  if (( argument_count == 1 )) &&
    [[ "$first_argument" == "-h" || "$first_argument" == "--help" ]]; then
    usage
    return 0
  fi
  if (( argument_count < 1 )); then
    usage >&2
    return 2
  fi
  if [[ "${DEVCONTAINER_VSCODE_LIVE:-0}" != "1" ]]; then
    printf '%s: live parity requires DEVCONTAINER_VSCODE_LIVE=1\n' \
      "$SCRIPT_NAME" >&2
    return 1
  fi

  local evidence_dir="$first_argument/vscode"
  shift
  local -a lanes=("$@")
  if (( ${#lanes[@]} == 0 )); then
    # Intentional word splitting turns the operator-owned subset into lanes.
    read -r -a lanes <<<"${DEVCONTAINER_VSCODE_LANES:-${ALL_LANES[*]}}"
  fi

  local lane
  for lane in "${lanes[@]}"; do
    validate_lane "$lane"
    python3 "$REPOSITORY_ROOT/Tools/parity/run_vscode.py" \
      "$lane" "$evidence_dir"
  done

  if (( ${#lanes[@]} == ${#ALL_LANES[@]} )); then
    python3 "$REPOSITORY_ROOT/Tools/parity/compare_results.py" \
      "$evidence_dir"
  fi
}

main "$@"
