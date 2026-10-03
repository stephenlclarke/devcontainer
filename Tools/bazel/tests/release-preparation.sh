#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: release-preparation.sh JUNIT_RUNNER PREPARATION_MODULE
# Execute only declared preparation tests; no downloads, installs or product builds.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 "$1" --directory "${2%/*}"
