#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -euo pipefail

if (( $# != 2 )); then
  printf 'usage: %s LANE EVIDENCE_DIR\n' "$0" >&2
  exit 2
fi

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
finalized_arguments=()
if [[ -n "${DEVCONTAINER_NATIVE_FINALIZED_DIRECTORY:-}${DEVCONTAINER_NATIVE_FINALIZATION_SHA256:-}${DEVCONTAINER_NATIVE_NOTARY_STATE:-}${DEVCONTAINER_NATIVE_SOURCE_COMMIT:-}" ]]; then
  finalized_arguments=(
    --finalized-directory "${DEVCONTAINER_NATIVE_FINALIZED_DIRECTORY:?Set finalized package directory}"
    --finalization-provenance-sha256 "${DEVCONTAINER_NATIVE_FINALIZATION_SHA256:?Set trusted finalization SHA-256}"
    --finalization-state "${DEVCONTAINER_NATIVE_NOTARY_STATE:?Set accepted notary state directory}"
    --expected-source-commit "${DEVCONTAINER_NATIVE_SOURCE_COMMIT:?Set exact finalized source SHA}"
  )
  if [[ "$1" == apple-stock ]]; then
    export DEVCONTAINER_CONTAINER_BIN="${DEVCONTAINER_RUNTIME_STOCK_BIN:-${DEVCONTAINER_CONTAINER_BIN:?Set qualified stock Container executable}}"
  elif [[ "$1" == container-compose ]]; then
    export DEVCONTAINER_CONTAINER_BIN="${DEVCONTAINER_RUNTIME_COMPOSE_BIN:-${DEVCONTAINER_CONTAINER_BIN:?Set qualified Compose provider Container executable}}"
  fi
fi
exec python3 "$repository_root/Tools/parity/run_lane.py" "$1" "$2" "${finalized_arguments[@]}"
