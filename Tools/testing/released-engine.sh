#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: released-engine.sh --campaign ID --lane apple-stock|container-compose
# One explicit released-binary case; preparation is a separate operation.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 "${BASH_SOURCE[0]%/*}/released_engine.py" "$@"
