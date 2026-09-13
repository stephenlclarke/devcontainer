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
launchctl_bin="${DEVCONTAINER_RUNTIME_LAUNCHCTL_BIN:-/bin/launchctl}"
apple_control_timeout_seconds="${DEVCONTAINER_RUNTIME_APPLE_COMMAND_TIMEOUT_SECONDS:-60}"
apple_start_timeout_seconds="${DEVCONTAINER_RUNTIME_APPLE_START_TIMEOUT_SECONDS:-${DEVCONTAINER_RUNTIME_APPLE_COMMAND_TIMEOUT_SECONDS:-150}}"
timeout_runner="$SCRIPT_DIRECTORY/run-with-timeout.py"
runtime_root_marker=".devcontainer-parity-runtime-root"
runtime_root_marker_content="devcontainer parity runtime root v1"

usage() {
  sed -n 's/^# *//p' "$SELF_PATH" | sed -n '/^USAGE:/,$p'
}

fail() {
  printf '%s: %s\n' "$SCRIPT_NAME" "$*" >&2
  return 1
}

runtime_status() {
  local executable="$1"

  run_with_timeout "$apple_control_timeout_seconds" \
    "$executable" system status --format json 2>/dev/null \
    | jq -er '.status'
}

# Deregister stock and Homebrew Container services when a CLI is unresponsive.
force_stop_runtime_services() {
  local domain
  local domain_state
  local service
  local service_list
  local service_namespace
  local user_id

  [[ -x "$launchctl_bin" ]] \
    || fail "launchctl executable is not usable: $launchctl_bin"
  user_id="$(id -u)"
  service_namespace="${CONTAINER_SERVICE_NAMESPACE:-com.apple.container}"
  for domain in "gui/$user_id" "user/$user_id"; do
    if ! domain_state="$($launchctl_bin print "$domain" 2>/dev/null)"; then
      fail "launchd domain is unavailable for forced cleanup: $domain"
    fi
    service_list="$(awk -v namespace="$service_namespace" \
      'index($3, namespace ".") == 1 ||
       (namespace == "com.apple.container" && $3 == "sh.brew.container") {
         print $3
       }' <<<"$domain_state" | sort -u || true)"
    while IFS= read -r service; do
      [[ -n "$service" ]] || continue
      "$launchctl_bin" bootout "$domain/$service" 2>/dev/null || true
      if "$launchctl_bin" print "$domain/$service" >/dev/null 2>&1; then
        fail "Container launchd service survived forced cleanup: $domain/$service"
      fi
    done <<<"$service_list"
  done
}

# Prepare a short, marker-owned application root for one isolated Apple lane.
prepare_runtime_root() {
  local executable="$1"
  local lane="$2"
  local candidate_runtime_binary
  local digest
  local identifier
  local install_root
  local marker
  local provider_directory
  local provider_socket
  local runtime_configuration
  local runtime_configuration_temporary
  local state_root_file

  [[ -n "${CONTAINER_APP_ROOT:-}" ]] \
    || fail "CONTAINER_APP_ROOT is required for isolated Apple parity"
  [[ "$CONTAINER_APP_ROOT" == /* ]] \
    || fail "CONTAINER_APP_ROOT must be absolute: $CONTAINER_APP_ROOT"
  [[ ! -L "$CONTAINER_APP_ROOT" ]] \
    || fail "runtime root must not be a symbolic link: $CONTAINER_APP_ROOT"
  marker="$CONTAINER_APP_ROOT/$runtime_root_marker"
  if [[ -e "$CONTAINER_APP_ROOT" && ! -f "$marker" ]]; then
    fail "runtime root exists without its parity marker: $CONTAINER_APP_ROOT"
  fi
  mkdir -p "$CONTAINER_APP_ROOT"
  chmod 700 "$CONTAINER_APP_ROOT"
  if [[ -f "$marker" ]]; then
    [[ ! -L "$marker" ]] || fail "runtime root marker must not be a symbolic link"
    [[ "$(<"$marker")" == "$runtime_root_marker_content" ]] \
      || fail "runtime root marker did not match: $marker"
  else
    printf '%s\n' "$runtime_root_marker_content" >"$marker"
  fi

  [[ -n "${XDG_CONFIG_HOME:-}" ]] \
    || fail "XDG_CONFIG_HOME is required for isolated Apple parity"
  [[ "$XDG_CONFIG_HOME" == "$CONTAINER_APP_ROOT/xdg" ]] \
    || fail "XDG_CONFIG_HOME must be owned by the parity runtime root"
  [[ ! -L "$XDG_CONFIG_HOME" && ! -L "$CONTAINER_APP_ROOT/config" ]] \
    || fail "runtime configuration directories must not be symbolic links"
  mkdir -p "$XDG_CONFIG_HOME" "$CONTAINER_APP_ROOT/config"
  chmod 700 "$XDG_CONFIG_HOME" "$CONTAINER_APP_ROOT/config"
  runtime_configuration="$CONTAINER_APP_ROOT/config/config.toml"
  runtime_configuration_temporary="$(
    mktemp "$CONTAINER_APP_ROOT/config/.config.toml.XXXXXX"
  )"
  printf '# Lane-owned configuration: use the selected binary defaults.\n' \
    >"$runtime_configuration_temporary"
  chmod 400 "$runtime_configuration_temporary"
  mv -f "$runtime_configuration_temporary" "$runtime_configuration"

  provider_socket="$CONTAINER_APP_ROOT/engine-provider/provider.sock"
  (( ${#provider_socket} < 104 )) \
    || fail "runtime root is too long for the provider Unix socket: $CONTAINER_APP_ROOT"

  if [[ "$lane" != "container-compose" ]]; then
    return 0
  fi
  install_root="${CONTAINER_INSTALL_ROOT:-$(cd "$(dirname "$executable")/.." && pwd -P)}"
  candidate_runtime_binary="$install_root/libexec/bin/$(basename "$executable")"
  if [[ -x "$candidate_runtime_binary" ]]; then
    executable="$candidate_runtime_binary"
  fi
  digest="$(shasum -a 256 "$executable" | awk '{print $1}')"
  identifier="${digest:0:8}-${digest:8:4}-${digest:12:4}-${digest:16:4}-${digest:20:12}"
  provider_directory="$CONTAINER_APP_ROOT/engine-provider"
  state_root_file="$provider_directory/state-root-id"
  [[ ! -L "$provider_directory" && ! -L "$state_root_file" ]] \
    || fail "runtime provider identity path must not be a symbolic link"
  mkdir -p "$provider_directory"
  chmod 700 "$provider_directory"
  if [[ -f "$state_root_file" ]]; then
    [[ "$(tr '[:upper:]' '[:lower:]' <"$state_root_file")" == "$identifier" ]] \
      || fail "runtime root identity does not match the selected runtime binary"
  else
    printf '%s\n' "$identifier" >"$state_root_file"
    chmod 600 "$state_root_file"
  fi
}

stop_runtime() {
  local executable="$1"

  [[ -x "$executable" ]] || return 0
  if run_with_timeout "$apple_control_timeout_seconds" \
    "$executable" system stop; then
    return
  fi
  force_stop_runtime_services \
    || fail "runtime stop command failed or timed out: $executable"
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

# Stop the Docker oracle so later candidate timings run on a quiet host.
stop_colima() {
  [[ -x "$colima_bin" ]] || return 0
  [[ -f "$timeout_runner" ]] || fail "timeout runner is missing: $timeout_runner"
  if run_with_timeout "$colima_command_timeout_seconds" \
    "$colima_bin" status >/dev/null 2>&1; then
    run_with_timeout "$colima_command_timeout_seconds" "$colima_bin" stop
    if run_with_timeout "$colima_command_timeout_seconds" \
      "$colima_bin" status >/dev/null 2>&1; then
      fail "Colima remained running after stop"
    fi
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
  local attempt
  local status

  if [[ "$lane" == "docker" ]]; then
    stop_all_apple_runtimes
    start_colima
    return
  fi

  # Candidate lanes establish their own quiet, Docker-free host state instead
  # of relying on a preceding oracle lane to have cleaned up successfully.
  stop_colima
  stop_all_apple_runtimes
  executable="$(selected_runtime "$lane")"
  [[ -x "$executable" ]] || fail "runtime executable is not usable: $executable"
  prepare_runtime_root "$executable" "$lane"
  if [[ "${DEVCONTAINER_RUNTIME_SKIP_SUDO:-0}" != "1" ]]; then
    sudo -n true \
      || fail "passwordless sudo is required to prepare the trusted runner"
  fi

  for attempt in 1 2 3; do
    if run_with_timeout "$apple_start_timeout_seconds" \
      "$executable" system start \
      --app-root "$CONTAINER_APP_ROOT" \
      --install-root "${CONTAINER_INSTALL_ROOT:-$(cd "$(dirname "$executable")/.." && pwd -P)}" \
      --enable-kernel-install --timeout 120; then
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

  [[ "$apple_control_timeout_seconds" =~ ^[1-9][0-9]*$ ]] \
    || fail "Apple runtime control timeout must be a positive integer"
  [[ "$apple_start_timeout_seconds" =~ ^[1-9][0-9]*$ ]] \
    || fail "Apple runtime start timeout must be a positive integer"

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
        stop_colima
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
