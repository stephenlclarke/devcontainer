#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.
#
# USAGE:
#   record-vhs-live-demo.sh TAPE OUTPUT

set -euo pipefail

if (( $# != 2 )); then
  sed -n 's/^# *//p' "${BASH_SOURCE[0]}" | sed -n '/^USAGE:/,$p' >&2
  exit 2
fi

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tape="$1"
output="$2"
container_bin="${DEVCONTAINER_DEMO_CONTAINER_BIN:-/usr/local/bin/container}"
vhs_bin="${VHS_BIN:-vhs}"
retry_count="${VHS_TRANSPORT_RETRIES:-3}"

for required in docker jq npx python3 shasum swift "$container_bin" "$vhs_bin"; do
  if [[ "$required" == */* ]]; then
    [[ -x "$required" ]] || {
      printf 'required executable is missing: %s\n' "$required" >&2
      exit 1
    }
  else
    command -v "$required" >/dev/null || {
      printf 'required executable is missing: %s\n' "$required" >&2
      exit 1
    }
  fi
done

cd "$repository_root"
"$vhs_bin" validate "$tape"
source_commit="$(git rev-parse HEAD)"
GIT_COMMIT="$source_commit" DEVCONTAINER_BUILD_LANE=release \
  swift build --disable-automatic-resolution -Xswiftc -warnings-as-errors \
    -c release
bin_directory="$(swift build --disable-automatic-resolution -c release --show-bin-path)"
demo_bin="$bin_directory/devcontainer"
engine_bin="$bin_directory/devcontainer-engine"
runtime_root="$(mktemp -d /private/tmp/devcontainer-demo.XXXXXX)"
socket_path="$runtime_root/docker.sock"
state_path="$runtime_root/state.sqlite"
engine_log="$runtime_root/devcontainer-engine.log"
engine_pid=""
started_runtime=false

cleanup() {
  local identifier
  local -a identifiers=()

  if [[ -S "$socket_path" ]]; then
    while IFS= read -r identifier; do
      [[ -n "$identifier" ]] && identifiers+=("$identifier")
    done < <(
      DOCKER_HOST="unix://${socket_path}" docker ps -aq \
        --filter "label=devcontainer.local_folder=${repository_root}/Examples/hello" \
        2>/dev/null || true
    )
    if (( ${#identifiers[@]} > 0 )); then
      # The IDs come only from the exact demo workspace label.
      DOCKER_HOST="unix://${socket_path}" docker rm -f \
        "${identifiers[@]}" >/dev/null 2>&1 || true
    fi
  fi
  if [[ -n "$engine_pid" ]] && kill -0 "$engine_pid" 2>/dev/null; then
    kill "$engine_pid" 2>/dev/null || true
    wait "$engine_pid" 2>/dev/null || true
  fi
  if [[ "$started_runtime" == true ]]; then
    "$container_bin" system stop >/dev/null 2>&1 || true
  fi
  rm -rf "$runtime_root"
}
trap cleanup EXIT

status="$("$container_bin" system status --format json 2>/dev/null || true)"
if ! jq -e '.status == "running"' <<<"$status" >/dev/null 2>&1; then
  "$container_bin" system start --enable-kernel-install --timeout 120
  started_runtime=true
fi

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
  )) | length == 1' >/dev/null

"$engine_bin" \
  --socket "$socket_path" \
  --state "$state_path" \
  --container "$container_bin" \
  >"$engine_log" 2>&1 &
engine_pid="$!"

for _ in $(seq 1 200); do
  if ! kill -0 "$engine_pid" 2>/dev/null; then
    cat "$engine_log" >&2
    printf 'devcontainer-engine exited before becoming ready\n' >&2
    exit 1
  fi
  if DOCKER_HOST="unix://${socket_path}" docker version >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
DOCKER_HOST="unix://${socket_path}" docker version >/dev/null

export DEVCONTAINER_DEMO_BIN="$demo_bin"
DEVCONTAINER_DEMO_CLI_VERSION="$(
  jq -r '.referencePins.devcontainersCli.version' Tests/Parity/manifest.json
)"
export DEVCONTAINER_DEMO_CLI_VERSION
export DEVCONTAINER_DEMO_CONTAINER_BIN="$container_bin"
export DEVCONTAINER_DEMO_ROOT="$repository_root"
export DEVCONTAINER_DEMO_SOCKET="$socket_path"

recorded=false
for attempt in $(seq 1 "$retry_count"); do
  vhs_log="$runtime_root/vhs-${attempt}.log"
  rm -f "$output" "$vhs_log"
  if "$vhs_bin" "$tape" >"$vhs_log" 2>&1; then
    cat "$vhs_log"
    [[ -s "$output" ]] || {
      printf 'VHS completed without producing %s\n' "$output" >&2
      exit 1
    }
    recorded=true
    break
  fi

  cat "$vhs_log" >&2
  if ! grep -Fq 'could not open ttyd' "$vhs_log"; then
    printf 'VHS failed after the live session began; refusing to replay it\n' >&2
    exit 1
  fi
  if (( attempt == retry_count )); then
    break
  fi
done

[[ "$recorded" == true ]] || {
  printf 'VHS could not establish its terminal transport\n' >&2
  exit 1
}
shasum -a 256 "$output"
