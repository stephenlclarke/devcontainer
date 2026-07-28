#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.
#
# USAGE:
#   runner-preflight.sh [docker|apple-stock|container-compose|all]

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_NAME="$(basename "$SELF_PATH")"
readonly SCRIPT_NAME
REPOSITORY_ROOT="$(cd "$(dirname "$SELF_PATH")/../.." && pwd -P)"
readonly REPOSITORY_ROOT

# Print command usage without probing a runtime.
usage() {
  sed -n 's/^# *//p' "$SELF_PATH" | sed -n '/^USAGE:/,$p'
}

# Require one executable and report a stable diagnostic.
need() {
  local command_name="$1"

  command -v "$command_name" >/dev/null || {
    printf '%s: required command is missing: %s\n' \
      "$SCRIPT_NAME" "$command_name" >&2
    return 1
  }
}

# Resolve an optional operator-selected executable without trusting aliases.
resolve_executable() {
  local configured="$1"
  local fallback="$2"

  if [[ -n "$configured" ]]; then
    [[ -x "$configured" ]] || {
      printf '%s: configured executable is not usable: %s\n' \
        "$SCRIPT_NAME" "$configured" >&2
      return 1
    }
    printf '%s\n' "$configured"
    return
  fi
  command -v "$fallback"
}

# Require one file to match its candidate-bound SHA-256 identity.
require_sha256() {
  local path="$1"
  local expected="$2"
  local actual

  actual="$(shasum -a 256 "$path" | cut -d ' ' -f 1)"
  if [[ "$actual" != "$expected" ]]; then
    printf '%s: SHA-256 differs for %s: expected %s, found %s\n' \
      "$SCRIPT_NAME" "$path" "$expected" "$actual" >&2
    return 1
  fi
}

# Reject unknown lane names before any runtime probe.
validate_lane() {
  local requested="$1"

  case "$requested" in
    docker | apple-stock | container-compose | all)
      ;;
    *)
      printf '%s: unsupported lane: %s\n' "$SCRIPT_NAME" "$requested" >&2
      return 1
      ;;
  esac
}

# Verify the exact tools required by one isolated parity lane.
main() {
  local argument_count="$#"
  local first_argument="${1:-}"

  if (( argument_count == 1 )) &&
    [[ "$first_argument" == "-h" || "$first_argument" == "--help" ]]; then
    usage
    return 0
  fi
  if (( argument_count > 1 )); then
    usage >&2
    return 2
  fi

  local lane="${first_argument:-all}"
  validate_lane "$lane"
  cd "$REPOSITORY_ROOT"

  if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    printf '%s: parity requires an arm64 Mac\n' "$SCRIPT_NAME" >&2
    return 1
  fi

  local command_name
  for command_name in code codesign jq make node npx python3 shasum swift xcodebuild; do
    need "$command_name"
  done

  local docker_bin
  local docker_compose_bin
  local container_compose_bin
  local container_bin
  docker_bin="$(resolve_executable "${DEVCONTAINER_DOCKER_BIN:-}" docker)"
  docker_compose_bin="$(
    resolve_executable "${DEVCONTAINER_DOCKER_COMPOSE_BIN:-}" docker-compose \
      2>/dev/null || true
  )"
  container_compose_bin="$(
    resolve_executable "${DEVCONTAINER_COMPOSE_BIN:-}" container-compose \
      2>/dev/null || true
  )"
  container_bin="$(resolve_executable "${DEVCONTAINER_CONTAINER_BIN:-}" container 2>/dev/null || true)"

  local docker_cli_version
  local docker_engine_version
  local docker_engine_api
  local docker_engine_commit
  local expected
  expected="$(jq -r '.referencePins.docker.cliSHA256' Tests/Parity/manifest.json)"
  require_sha256 "$docker_bin" "$expected"
  docker_cli_version="$("$docker_bin" version --format '{{.Client.Version}}')"
  docker_engine_version="$("$docker_bin" version --format '{{.Server.Version}}')"
  docker_engine_api="$("$docker_bin" version --format '{{.Server.APIVersion}}')"
  docker_engine_commit="$("$docker_bin" version --format '{{.Server.GitCommit}}')"
  [[ "$docker_cli_version" == "$(
    jq -r '.referencePins.docker.cliVersion' Tests/Parity/manifest.json
  )" ]]
  [[ "$docker_engine_version" == "$(
    jq -r '.referencePins.docker.engineVersion' Tests/Parity/manifest.json
  )" ]]
  [[ "$docker_engine_api" == "$(
    jq -r '.referencePins.docker.engineApiVersion' Tests/Parity/manifest.json
  )" ]]
  [[ "$docker_engine_commit" == "$(
    jq -r '.referencePins.docker.engineCommit' Tests/Parity/manifest.json
  )" ]]
  "$docker_bin" info >/dev/null

  local devcontainers_version
  devcontainers_version="$(
    jq -r '.referencePins.devcontainersCli.version' Tests/Parity/manifest.json
  )"
  npx --yes "@devcontainers/cli@${devcontainers_version}" --version \
    | grep -Fx "$devcontainers_version"

  if [[ "$lane" == "docker" || "$lane" == "all" ]]; then
    need colima
    colima status >/dev/null
    [[ "$(docker context show)" == "colima" ]]
    [[ "$(
      colima ssh -- sha256sum /usr/bin/dockerd | cut -d ' ' -f 1
    )" == "$(
      jq -r '.referencePins.docker.engineSHA256' Tests/Parity/manifest.json
    )" ]]
    [[ -n "$docker_compose_bin" ]] || {
      printf '%s: Docker Compose is required for %s parity\n' \
        "$SCRIPT_NAME" "$lane" >&2
      return 1
    }
    require_sha256 "$docker_compose_bin" "$(
      jq -r '.referencePins.docker.composeSHA256' Tests/Parity/manifest.json
    )"
    "$docker_compose_bin" version --short | grep -Fx "$(
      jq -r '.referencePins.docker.composeVersion' Tests/Parity/manifest.json
    )"
  fi

  local expected_code_version
  local expected_code_commit
  local actual_code_version
  expected_code_version="$(
    jq -r '.referencePins.vscode.version' Tests/Parity/manifest.json
  )"
  expected_code_commit="$(
    jq -r '.referencePins.vscode.commit' Tests/Parity/manifest.json
  )"
  actual_code_version="$(code --version)"
  grep -Fx "$expected_code_version" <<<"$actual_code_version"
  grep -Fx "$expected_code_commit" <<<"$actual_code_version"

  [[ "$(uname -m)" == "$(
    jq -r '.referencePins.releaseHost.architecture' Tests/Parity/manifest.json
  )" ]]
  [[ "$(sw_vers -productVersion)" == "$(
    jq -r '.referencePins.releaseHost.macOSProductVersion' Tests/Parity/manifest.json
  )" ]]
  [[ "$(sw_vers -buildVersion)" == "$(
    jq -r '.referencePins.releaseHost.macOSBuildVersion' Tests/Parity/manifest.json
  )" ]]
  xcodebuild -version | grep -Fx "Xcode $(
    jq -r '.referencePins.releaseHost.xcodeVersion' Tests/Parity/manifest.json
  )"
  xcodebuild -version | grep -Fx "Build version $(
    jq -r '.referencePins.releaseHost.xcodeBuildVersion' Tests/Parity/manifest.json
  )"
  swift --version | grep -F "Apple Swift version $(
    jq -r '.referencePins.releaseHost.swiftVersion' Tests/Parity/manifest.json
  )"

  if [[ "$lane" != "docker" ]]; then
    [[ -n "$container_bin" ]] || {
      printf '%s: Apple container is required for %s parity\n' \
        "$SCRIPT_NAME" "$lane" >&2
      return 1
    }
    "$container_bin" system status --format json | jq -e '.status == "running"'
  fi

  if [[ "$lane" == "apple-stock" ]]; then
    "$container_bin" system version --format json | jq -e \
      --arg version "$(
        jq -r '.referencePins.appleContainer.stableVersion' Tests/Parity/manifest.json
      )" \
      --arg commit "$(
        jq -r '.referencePins.appleContainer.stableCommit' Tests/Parity/manifest.json
      )" \
      'map(select(
        .appName == "container" and
        .version == $version and
        .commit == $commit and
        ((.distribution // "apple") == "apple")
      )) | length == 1'
  fi

  if [[ "$lane" == "container-compose" || "$lane" == "all" ]]; then
    [[ -n "$container_compose_bin" ]] || {
      printf '%s: container-compose is required for %s parity\n' \
        "$SCRIPT_NAME" "$lane" >&2
      return 1
    }
    "$container_compose_bin" version --format json | jq -e \
      --arg version "$(
        jq -r '.referencePins.containerCompose.stableVersion' Tests/Parity/manifest.json
      )" \
      --arg commit "$(
        jq -r '.referencePins.containerCompose.stableCommit' Tests/Parity/manifest.json
      )" \
      '.version == $version and
       .commit == $commit and
       .source == "stephenlclarke/container-compose"'
  fi

  printf 'trusted %s parity runner preflight passed\n' "$lane"
}

main "$@"
