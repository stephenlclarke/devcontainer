#!/usr/bin/env bash
# USAGE:
#   sign-and-notarize.sh STAGE_DIRECTORY EVIDENCE_FILE \
#     --candidate-receipt PATH --candidate-sha256 SHA256 \
#     --stage-provenance PATH --stage-provenance-sha256 SHA256 \
#     [--state-directory INTERNAL_PATH] [--scratch-directory SSD_PATH] [--resume]
#
# Sign all six native executables using DEVCONTAINER_SIGNING_IDENTITY and
# DEVCONTAINER_SIGNING_TEAM_ID; submit using DEVCONTAINER_NOTARY_PROFILE.
# Durable state defaults to internal ContainerFamily retained storage, keyed by
# both candidate receipt and stage provenance SHA-256. Exact
# stage provenance, submitted bytes and IDs survive SSD cleanup. Resume queries the retained ID;
# unknown submission outcomes never trigger a second upload. No publication.

#===----------------------------------------------------------------------===#
# Copyright 2026 devcontainer project authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#===----------------------------------------------------------------------===#

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
TOOL_DIRECTORY="$(cd "$(dirname "$SELF_PATH")" && pwd -P)"
readonly TOOL_DIRECTORY
exec python3 "$TOOL_DIRECTORY/native_signing.py" "$@"
