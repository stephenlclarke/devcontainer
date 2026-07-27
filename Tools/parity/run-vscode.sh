#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -euo pipefail

if (( $# != 1 )); then
  printf 'usage: %s EVIDENCE_DIR\n' "$0" >&2
  exit 2
fi

evidence_dir="$1/vscode"
mkdir -p "$evidence_dir"

if [[ "${DEVCONTAINER_VSCODE_LIVE:-0}" != "1" ]]; then
  printf 'VS Code live parity requires DEVCONTAINER_VSCODE_LIVE=1\n' >&2
  exit 1
fi

command -v code >/dev/null || {
  printf 'VS Code command-line launcher is required\n' >&2
  exit 1
}

code --version >"$evidence_dir/version.txt"
if ! code --list-extensions --show-versions \
  | grep -E '^ms-vscode-remote[.]remote-containers@' \
  >"$evidence_dir/extension.txt"; then
  printf 'the VS Code Dev Containers extension is required\n' >&2
  exit 1
fi

# Interactive attach/rebuild validation is driven by the isolated release
# runner's VS Code automation. That driver must write its signed result before
# this gate is called; a missing result is a release failure, never a skip.
result="${DEVCONTAINER_VSCODE_RESULT:-$evidence_dir/live-result.json}"
python3 - "$result" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(f"VS Code automation result is missing: {path}")
value = json.loads(path.read_text(encoding="utf-8"))
required = {
    "open": True,
    "attach": True,
    "integratedCommand": True,
    "forwardPort": True,
    "rebuild": True,
    "reopen": True,
    "cleanup": True,
}
if any(value.get(key) is not expected for key, expected in required.items()):
    raise SystemExit(f"VS Code parity result failed: {value}")
print("VS Code live parity passed")
PY
