#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.
#
# USAGE:
#   require-quiet-host.sh EVIDENCE_DIRECTORY

set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_NAME="$(basename "$SELF_PATH")"
readonly SCRIPT_NAME

readonly PGREP_BIN="${DEVCONTAINER_QUIET_HOST_PGREP_BIN:-/usr/bin/pgrep}"
readonly PMSET_BIN="${DEVCONTAINER_QUIET_HOST_PMSET_BIN:-/usr/bin/pmset}"
readonly PS_BIN="${DEVCONTAINER_QUIET_HOST_PS_BIN:-/bin/ps}"
readonly SLEEP_BIN="${DEVCONTAINER_QUIET_HOST_SLEEP_BIN:-/bin/sleep}"
readonly SYSCTL_BIN="${DEVCONTAINER_QUIET_HOST_SYSCTL_BIN:-/usr/sbin/sysctl}"

# Print the maintained usage block to the requested output stream.
usage() {
  local destination="${1:-2}"

  sed -n 's/^# *//p' "$SELF_PATH" | sed -n '/^USAGE:/,$p' >&"$destination"
}

# Report one validation failure without terminating callers that are collecting
# an exit status explicitly.
fail() {
  printf '%s: %s\n' "$SCRIPT_NAME" "$*" >&2
  return 1
}

# Require every platform tool through an explicit executable path.
require_executable() {
  local path="$1"

  [[ -x "$path" ]] || fail "required executable is unavailable: $path"
}

# Reject malformed wait and polling settings before creating evidence.
validate_nonnegative_integer() {
  local label="$1"
  local value="$2"

  [[ "$value" =~ ^[0-9]+$ ]] || fail "$label must be a non-negative integer"
}

# Record known compilation and release processes that would invalidate timing.
record_process_matches() {
  local output="$1"
  local found=0
  local matches=""
  local process_name
  local query_status=0

  : >"$output"
  for process_name in \
    swift-build swift-test swift-frontend swiftc clang xcodebuild nextflow \
    ninja cmake; do
    query_status=0
    matches="$($PGREP_BIN -x "$process_name" 2>/dev/null)" \
      || query_status=$?
    case "$query_status" in
      0)
        while IFS= read -r process_id; do
          [[ -n "$process_id" ]] \
            && printf '%s\t%s\n' "$process_name" "$process_id" >>"$output"
        done <<<"$matches"
        printf 'competing build process is active: %s\n' "$process_name" >&2
        found=1
        ;;
      1)
        ;;
      *)
        printf '%s: could not inspect process name: %s\n' \
          "$SCRIPT_NAME" "$process_name" >&2
        return 2
        ;;
    esac
  done

  query_status=0
  matches="$($PGREP_BIN -f \
    '/scripts/CONTAINER_STACK_RELEASE[.]sh release|make release VERSION_SELECTOR=' \
    2>/dev/null)" || query_status=$?
  case "$query_status" in
    0)
      while IFS= read -r process_id; do
        [[ -n "$process_id" ]] \
          && printf 'container-family-release\t%s\n' "$process_id" >>"$output"
      done <<<"$matches"
      printf 'a competing Container-family release is active\n' >&2
      found=1
      ;;
    1)
      ;;
    *)
      printf '%s: could not inspect Container-family release processes\n' \
        "$SCRIPT_NAME" >&2
      return 2
      ;;
  esac

  return "$found"
}

main() {
  local argument="${1:-}"
  if [[ "$#" -eq 1 && ("${argument}" == "-h" || "${argument}" == "--help") ]]; then
    usage 1
    return 0
  fi
  if [[ "$#" -ne 1 ]]; then
    usage
    return 2
  fi

  local evidence_directory="${argument}"
  local wait_seconds="${DEVCONTAINER_QUIET_HOST_WAIT_SECONDS:-3600}"
  local poll_seconds="${DEVCONTAINER_QUIET_HOST_POLL_SECONDS:-10}"
  validate_nonnegative_integer "quiet-host wait seconds" "$wait_seconds"
  validate_nonnegative_integer "quiet-host poll seconds" "$poll_seconds"
  ((poll_seconds > 0)) || fail "quiet-host poll seconds must be positive"

  local executable
  for executable in \
    "$PGREP_BIN" "$PMSET_BIN" "$PS_BIN" "$SLEEP_BIN" "$SYSCTL_BIN"; do
    require_executable "$executable"
  done

  install -d -m 0700 "$evidence_directory"
  rm -f -- "$evidence_directory/quiet-host.tsv"
  local cpu_count
  cpu_count="$($SYSCTL_BIN -n hw.ncpu)"
  [[ "$cpu_count" =~ ^[1-9][0-9]*$ ]] \
    || fail "could not determine a positive logical CPU count"

  local attempt=0
  local deadline=$((SECONDS + wait_seconds))
  local load_one=""
  local load_limit=""
  local process_status=0
  while :; do
    attempt=$((attempt + 1))
    "$PMSET_BIN" -g therm \
      >"$evidence_directory/thermal-${attempt}.txt" 2>&1 || true
    "$SYSCTL_BIN" -n vm.loadavg \
      >"$evidence_directory/load-${attempt}.txt"
    "$PS_BIN" -Ao pid,ppid,%cpu,%mem,etime,comm -r \
      >"$evidence_directory/processes-${attempt}.txt"

    load_one="$(awk '{ print $2 }' \
      "$evidence_directory/load-${attempt}.txt")"
    [[ "$load_one" =~ ^[0-9]+([.][0-9]+)?$ ]] \
      || fail "could not parse the one-minute load average"
    load_limit="$(awk -v cpus="$cpu_count" 'BEGIN {
      limit = cpus / 4
      if (limit > 2) {
        limit = 2
      }
      printf "%.3f", limit
    }')"

    process_status=0
    record_process_matches \
      "$evidence_directory/competing-${attempt}.txt" || process_status=$?
    if ((process_status > 1)); then
      return "$process_status"
    fi

    if ((process_status == 0)) && awk -v load="$load_one" \
      -v limit="$load_limit" 'BEGIN { exit !(load <= limit) }'; then
      {
        printf 'result\tquiet\n'
        printf 'attempt\t%s\n' "$attempt"
        printf 'logicalCPUs\t%s\n' "$cpu_count"
        printf 'oneMinuteLoad\t%s\n' "$load_one"
        printf 'loadLimit\t%s\n' "$load_limit"
        printf 'loadPolicy\tmin(logicalCPUs/4,2.0)\n'
      } >"$evidence_directory/quiet-host.tsv"
      printf 'quiet host established: load=%s limit=%s attempts=%s\n' \
        "$load_one" "$load_limit" "$attempt"
      return 0
    fi

    if ((SECONDS >= deadline)); then
      fail "host did not become quiet: load=$load_one limit=$load_limit attempts=$attempt"
    fi
    "$SLEEP_BIN" "$poll_seconds"
  done
}

main "$@"
