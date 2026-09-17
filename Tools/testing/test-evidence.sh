#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: test-evidence.sh
# Bazel owns selection, caching and reports; this runs only the evidence tests.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 "${BASH_SOURCE[0]%/*}/run_tests.py"
