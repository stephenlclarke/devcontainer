#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: released_engine_runfiles_smoke.sh
# Verify the released Engine entry point can import its declared Bazel runfiles.
set -euo pipefail

readonly SCRIPT_DIRECTORY="${BASH_SOURCE[0]%/*}"
help_output="$("${SCRIPT_DIRECTORY}/released-engine.sh" --help)"

if [[ "${help_output}" != *"--campaign CAMPAIGN"* ]]; then
    printf '%s\n' "released Engine help omitted its campaign option" >&2
    exit 1
fi

printf '%s\n' "released Engine runfiles smoke passed"
