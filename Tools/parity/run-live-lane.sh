#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.
#
# USAGE:
#   run-live-lane.sh docker|apple-stock|container-compose

set -Eeuo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIRECTORY="$(cd "$(dirname "${SELF_PATH}")" && pwd -P)"
readonly SCRIPT_DIRECTORY
REPOSITORY="$(cd "${SCRIPT_DIRECTORY}/../.." && pwd -P)"
readonly REPOSITORY

# Resolved from the verified repository root rather than the current directory.
# shellcheck disable=SC1091
source "${REPOSITORY}/Tools/ci/container-runtime-lock.sh"

lane="${1:-}"
runtime_cleanup_required=0

# Preserve primary failures while always scrubbing evidence and releasing state.
cleanup() {
  local original_status="$?"
  local cleanup_status=0
  trap - EXIT

  if [[ -d "${REPOSITORY}/.build/parity/vscode/${lane}" ]]; then
    python3 "${REPOSITORY}/Tools/parity/scrub_vscode_evidence.py" \
      "${REPOSITORY}/.build/parity/vscode/${lane}" || cleanup_status="$?"
  fi
  if [[ "${runtime_cleanup_required}" == "1" ]]; then
    "${REPOSITORY}/Tools/parity/runner-runtime.sh" stop "${lane}" \
      || cleanup_status="$?"
  fi
  release_container_runtime_lock || cleanup_status="$?"

  if ((original_status != 0)); then
    exit "${original_status}"
  fi
  exit "${cleanup_status}"
}

usage() {
  sed -n 's/^# *//p' "${SELF_PATH}" | sed -n '/^USAGE:/,$p'
}

main() {
  if [[ "$#" -ne 1 ]]; then
    usage >&2
    return 2
  fi
  case "${lane}" in
    docker | apple-stock | container-compose)
      ;;
    *)
      printf 'unsupported parity lane: %s\n' "${lane}" >&2
      return 2
      ;;
  esac

  cd "${REPOSITORY}"
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  acquire_container_runtime_lock
  runtime_cleanup_required=1
  Tools/parity/runner-runtime.sh start "${lane}"
  Tools/parity/runner-preflight.sh "${lane}"
  Tools/parity/require-quiet-host.sh \
    ".build/parity/host-quiet/${lane}/cli"
  make "parity-${lane}"
  Tools/parity/require-quiet-host.sh \
    ".build/parity/host-quiet/${lane}/vscode"
  DEVCONTAINER_VSCODE_LIVE=1 make "parity-vscode-${lane}"
}

main "$@"
