#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: released-engine.sh --campaign ID --lane docker|apple-stock|container-compose [--fixture E01-engine-negotiation|E02-container-lifecycle|E03-exec-streams|E04-image-build|E05-archive-copy|E06-network-volume]
# One explicit released-binary case; preparation is a separate operation.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 "${BASH_SOURCE[0]%/*}/released_engine.py" "$@"
