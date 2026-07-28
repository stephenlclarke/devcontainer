#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.
#
# USAGE:
#   runner-runtime.sh start|stop docker|apple-stock|container-compose

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_NAME="$(basename "$SELF_PATH")"
readonly SCRIPT_NAME

stock_bin="${DEVCONTAINER_RUNTIME_STOCK_BIN:-/usr/local/bin/container}"
compose_bin="${DEVCONTAINER_RUNTIME_COMPOSE_BIN:-/opt/homebrew/bin/container}"

usage() {
  sed -n 's/^# *//p' "$SELF_PATH" | sed -n '/^USAGE:/,$p'
}

fail() {
  printf '%s: %s\n' "$SCRIPT_NAME" "$*" >&2
  return 1
}

runtime_status() {
  local executable="$1"

  "$executable" system status --format json 2>/dev/null \
    | jq -er '.status'
}

stop_runtime() {
  local executable="$1"
  local status

  [[ -x "$executable" ]] || return 0
  status="$(runtime_status "$executable" 2>/dev/null || true)"
  case "$status" in
    running | starting | stopping)
      "$executable" system stop
      ;;
    *)
      ;;
  esac
}

stop_all_apple_runtimes() {
  stop_runtime "$stock_bin"
  if [[ "$compose_bin" != "$stock_bin" ]]; then
    stop_runtime "$compose_bin"
  fi
}

selected_runtime() {
  local lane="$1"

  case "$lane" in
    apple-stock)
      printf '%s\n' "${DEVCONTAINER_CONTAINER_BIN:-$stock_bin}"
      ;;
    container-compose)
      printf '%s\n' "${DEVCONTAINER_CONTAINER_BIN:-$compose_bin}"
      ;;
    *)
      fail "lane does not select an Apple runtime: $lane"
      ;;
  esac
}

start_runtime() {
  local lane="$1"
  local executable
  local status

  stop_all_apple_runtimes
  if [[ "$lane" == "docker" ]]; then
    return
  fi

  executable="$(selected_runtime "$lane")"
  [[ -x "$executable" ]] || fail "runtime executable is not usable: $executable"
  if [[ "${DEVCONTAINER_RUNTIME_SKIP_SUDO:-0}" != "1" ]]; then
    sudo -n true \
      || fail "passwordless sudo is required to prepare the trusted runner"
  fi

  "$executable" system start --enable-kernel-install --timeout 120
  status="$(runtime_status "$executable")"
  [[ "$status" == "running" ]] \
    || fail "runtime did not reach running state: $status"
}

main() {
  if [[ "$#" -ne 2 ]]; then
    usage >&2
    return 2
  fi
  local operation="$1"
  local lane="$2"

  case "$lane" in
    docker | apple-stock | container-compose)
      ;;
    *)
      fail "unsupported lane: $lane"
      ;;
  esac

  case "$operation" in
    start)
      start_runtime "$lane"
      ;;
    stop)
      if [[ "$lane" == "docker" ]]; then
        stop_all_apple_runtimes
      else
        stop_runtime "$(selected_runtime "$lane")"
      fi
      ;;
    *)
      fail "unsupported operation: $operation"
      ;;
  esac
}

main "$@"
