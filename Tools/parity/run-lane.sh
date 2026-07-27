#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -euo pipefail

if (( $# != 2 )); then
  printf 'usage: %s LANE EVIDENCE_DIR\n' "$0" >&2
  exit 2
fi

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
exec python3 "$repository_root/Tools/parity/run_lane.py" "$1" "$2"
