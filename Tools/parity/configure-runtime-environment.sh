#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.
#
# USAGE:
#   configure-runtime-environment.sh apple-stock|container-compose

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_NAME="$(basename "$SELF_PATH")"
readonly SCRIPT_NAME

fail() {
  printf '%s: %s\n' "$SCRIPT_NAME" "$*" >&2
  return 1
}

emit_environment() {
  local key="$1"
  local value="$2"

  if [[ -n "${GITHUB_ENV:-}" ]]; then
    printf '%s=%s\n' "$key" "$value" >>"$GITHUB_ENV"
  else
    printf 'export %s=%q\n' "$key" "$value"
  fi
}

main() {
  local container_bin
  local digest
  local install_root
  local lane
  local namespace
  local runtime_binary
  local runtime_root

  [[ "$#" -eq 1 ]] || {
    sed -n 's/^# *//p' "$SELF_PATH" | sed -n '/^USAGE:/,$p' >&2
    return 2
  }
  lane="$1"
  case "$lane" in
    apple-stock | container-compose)
      ;;
    *)
      fail "unsupported Apple runtime lane: $lane"
      ;;
  esac
  container_bin="${DEVCONTAINER_CONTAINER_BIN:-}"
  [[ -x "$container_bin" ]] \
    || fail "DEVCONTAINER_CONTAINER_BIN is not executable: $container_bin"
  install_root="$(cd "$(dirname "$container_bin")/.." && pwd -P)"
  runtime_binary="$container_bin"
  if [[ -x "$install_root/libexec/bin/$(basename "$container_bin")" ]]; then
    runtime_binary="$install_root/libexec/bin/$(basename "$container_bin")"
  fi
  digest="$(shasum -a 256 "$runtime_binary" | awk '{print $1}')"
  runtime_root="/tmp/dcparity-${lane}-${digest:0:12}"
  namespace="com.apple.container"
  if [[ "$lane" == "container-compose" ]]; then
    namespace="io.github.stephenlclarke.devcontainer.parity.${digest:0:12}"
  fi

  emit_environment CONTAINER_APP_ROOT "$runtime_root"
  emit_environment CONTAINER_INSTALL_ROOT "$install_root"
  emit_environment CONTAINER_SERVICE_NAMESPACE "$namespace"
  emit_environment XDG_CONFIG_HOME "$runtime_root/xdg"
}

main "$@"
