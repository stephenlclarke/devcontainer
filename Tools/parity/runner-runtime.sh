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
SCRIPT_DIRECTORY="$(cd "$(dirname "$SELF_PATH")" && pwd)"
readonly SCRIPT_DIRECTORY

stock_bin="${DEVCONTAINER_RUNTIME_STOCK_BIN:-/usr/local/bin/container}"
compose_bin="${DEVCONTAINER_RUNTIME_COMPOSE_BIN:-/opt/homebrew/opt/container/bin/container}"
colima_bin="${DEVCONTAINER_RUNTIME_COLIMA_BIN:-/opt/homebrew/bin/colima}"
colima_command_timeout_seconds="${DEVCONTAINER_RUNTIME_COLIMA_COMMAND_TIMEOUT_SECONDS:-120}"
timeout_runner="$SCRIPT_DIRECTORY/run-with-timeout.py"

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

run_with_timeout() {
  local timeout_seconds="$1"
  shift

  python3 "$timeout_runner" "$timeout_seconds" "$@"
}

start_colima() {
  local attempt

  [[ -x "$colima_bin" ]] || fail "Colima executable is not usable: $colima_bin"
  [[ -f "$timeout_runner" ]] || fail "timeout runner is missing: $timeout_runner"
  command -v python3 >/dev/null || fail "python3 is required to bound Colima commands"
  [[ "$colima_command_timeout_seconds" =~ ^[1-9][0-9]*$ ]] \
    || fail "Colima command timeout must be a positive integer"
  if run_with_timeout "$colima_command_timeout_seconds" \
    "$colima_bin" status >/dev/null 2>&1; then
    return
  fi
  for attempt in 1 2 3; do
    if run_with_timeout "$colima_command_timeout_seconds" "$colima_bin" start \
      && run_with_timeout "$colima_command_timeout_seconds" \
        "$colima_bin" status >/dev/null; then
      return
    fi
    if (( attempt < 3 )); then
      sleep 1
    fi
  done
  fail "Colima did not reach running state after 3 attempts"
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
  local attempt
  local status

  stop_all_apple_runtimes
  start_colima
  if [[ "$lane" == "docker" ]]; then
    return
  fi

  executable="$(selected_runtime "$lane")"
  [[ -x "$executable" ]] || fail "runtime executable is not usable: $executable"
  if [[ "${DEVCONTAINER_RUNTIME_SKIP_SUDO:-0}" != "1" ]]; then
    sudo -n true \
      || fail "passwordless sudo is required to prepare the trusted runner"
  fi

  for attempt in 1 2 3; do
    if "$executable" system start --enable-kernel-install --timeout 120; then
      status="$(runtime_status "$executable" 2>/dev/null || true)"
      if [[ "$status" == "running" ]]; then
        return
      fi
    fi
    if (( attempt < 3 )); then
      stop_all_apple_runtimes
      sleep 1
    fi
  done
  fail "runtime did not reach running state after 3 attempts: ${status:-unavailable}"
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
