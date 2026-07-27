#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  printf 'parity requires an arm64 Mac\n' >&2
  exit 1
fi

for command_name in container docker jq make node npx python3 swift; do
  command -v "$command_name" >/dev/null || {
    printf 'required command is missing: %s\n' "$command_name" >&2
    exit 1
  }
done

container system status >/dev/null
docker info >/dev/null
npx --yes @devcontainers/cli@0.88.0 --version | grep -F '0.88.0'

if [[ "${DEVCONTAINER_REQUIRE_CONTAINER_COMPOSE:-1}" == "1" ]]; then
  command -v container-compose >/dev/null || {
    printf 'container-compose is required for the provider lane\n' >&2
    exit 1
  }
  container-compose version --format json | jq -e '.version and .source'
fi

printf 'trusted parity runner preflight passed\n'
