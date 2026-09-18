#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: package-smoke.sh JUNIT_RUNNER TEST_MODULE ARCHIVE RECEIPT
# Inspect and run the declared unsigned package on SSD without installation.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
export DEVCONTAINER_TEST_ARCHIVE="$3" DEVCONTAINER_TEST_RECEIPT="$4"
exec /usr/bin/python3 "$1" --directory "${2%/*}"
