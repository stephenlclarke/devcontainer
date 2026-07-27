#!/usr/bin/env bash
# USAGE:
#   run-cli-coverage.sh SWIFT_BIN_DIRECTORY
#
# Exercise instrumented command entry points without requiring a live runtime.

set -euo pipefail

if (( $# != 1 )); then
  printf 'usage: %s SWIFT_BIN_DIRECTORY\n' "$(basename "$0")" >&2
  exit 64
fi

readonly BIN_DIRECTORY="$1"
readonly DEVCONTAINER="$BIN_DIRECTORY/devcontainer"
readonly DEVCONTAINER_COMPOSE="$BIN_DIRECTORY/devcontainer-compose"
readonly PROFILE_DIRECTORY="$BIN_DIRECTORY/codecov"
TEMPORARY_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/devcontainer-cli-coverage.XXXXXX")"
readonly TEMPORARY_DIRECTORY
readonly CONFIGURATION="$TEMPORARY_DIRECTORY/config.toml"
readonly STATE_DATABASE="$TEMPORARY_DIRECTORY/state.sqlite"
readonly FAKE_CONTAINER="$TEMPORARY_DIRECTORY/container"
readonly FAKE_DOCKER="$TEMPORARY_DIRECTORY/docker"
readonly FAKE_COMPOSE="$TEMPORARY_DIRECTORY/container-compose"
readonly INVALID_UTF8_MARKER="$TEMPORARY_DIRECTORY/invalid-utf8"

for executable in "$DEVCONTAINER" "$DEVCONTAINER_COMPOSE"; do
  if [[ ! -x "$executable" ]]; then
    printf 'instrumented executable is missing: %s\n' "$executable" >&2
    exit 2
  fi
done
mkdir -p "$PROFILE_DIRECTORY"
export LLVM_PROFILE_FILE="$PROFILE_DIRECTORY/devcontainer-cli-%p.profraw"

# Remove only the private directory returned by mktemp.
# shellcheck disable=SC2329  # Invoked indirectly by the EXIT trap.
cleanup() {
  rm -rf "$TEMPORARY_DIRECTORY"
}
trap cleanup EXIT

# Run a command that must succeed while keeping fixture output quiet.
run_success() {
  "$@" >/dev/null
}

# Run a command that must fail while keeping expected diagnostics quiet.
run_failure() {
  if "$@" >/dev/null 2>&1; then
    printf 'expected command to fail:' >&2
    printf ' %q' "$@" >&2
    printf '\n' >&2
    exit 1
  fi
}

# The quoted positional parameters belong to the generated fixture.
# shellcheck disable=SC2016
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'case "$*" in' \
  '  "system version --format json") printf "%s\n" '\''{"version":"fixture"}'\'' ;;' \
  '  "system status")' \
  "    if [[ -f '$INVALID_UTF8_MARKER' ]]; then" \
  '      printf "\377" >&2' \
  '      exit 23' \
  '    fi' \
  '    printf "%s\n" "running"' \
  '    ;;' \
  '  *) exit 64 ;;' \
  'esac' >"$FAKE_CONTAINER"

# The quoted positional parameter belongs to the generated fixture.
# shellcheck disable=SC2016
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'if [[ "${1-}" == "version" ]]; then' \
  '  printf "%s\n" '\''{"version":"0.1.0","source":"stephenlclarke/container-compose"}'\''' \
  'else' \
  '  printf "%s\n" "compose-fixture"' \
  'fi' >"$FAKE_COMPOSE"

# The quoted positional parameters belong to the generated fixture.
# shellcheck disable=SC2016
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'if [[ "${1-}" != "compose" ]]; then exit 64; fi' \
  'if [[ "${2-}" == "version" ]]; then printf "%s\n" '\''{"Version":"fixture"}'\''; fi' \
  >"$FAKE_DOCKER"

chmod 700 "$FAKE_CONTAINER" "$FAKE_COMPOSE" "$FAKE_DOCKER"

readonly COMMON_ENV=(
  "DEVCONTAINER_CONFIG=$CONFIGURATION"
  "DEVCONTAINER_STATE=$STATE_DATABASE"
  "DEVCONTAINER_CONTAINER_BIN=$FAKE_CONTAINER"
)

run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" version --short
run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" version --format pretty
run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" version --format json
run_failure env "${COMMON_ENV[@]}" "$DEVCONTAINER" version --format invalid

run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" context \
  --socket "$TEMPORARY_DIRECTORY/docker.sock" --format shell
run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" context \
  --socket "$TEMPORARY_DIRECTORY/docker.sock" --format value
run_failure env "${COMMON_ENV[@]}" "$DEVCONTAINER" context --format invalid

run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" configure \
  --config "$CONFIGURATION" \
  --backend stock \
  --compose-provider docker \
  --socket "$TEMPORARY_DIRECTORY/docker.sock" \
  --strict
run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" configure \
  --config "$CONFIGURATION" --no-strict
run_failure env "${COMMON_ENV[@]}" "$DEVCONTAINER" configure \
  --config "$CONFIGURATION" --backend invalid
run_failure env "${COMMON_ENV[@]}" "$DEVCONTAINER" configure \
  --config "$CONFIGURATION" --compose-provider invalid

run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" backend set \
  --project fixture --state "$STATE_DATABASE" stock
run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" backend show \
  --project fixture --state "$STATE_DATABASE"
run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" backend reset \
  --project fixture --state "$STATE_DATABASE"
run_failure env "${COMMON_ENV[@]}" "$DEVCONTAINER" backend show \
  --project fixture --state "$STATE_DATABASE"
run_failure env "${COMMON_ENV[@]}" "$DEVCONTAINER" backend set \
  --project fixture --state "$STATE_DATABASE" invalid

run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" doctor \
  --container "$FAKE_CONTAINER" \
  --socket "$TEMPORARY_DIRECTORY/missing.sock" \
  --compose "$FAKE_COMPOSE" \
  --format pretty
run_success env "${COMMON_ENV[@]}" "$DEVCONTAINER" doctor \
  --container "$FAKE_CONTAINER" \
  --socket "$TEMPORARY_DIRECTORY/missing.sock" \
  --compose "$FAKE_COMPOSE" \
  --format json
run_failure env "${COMMON_ENV[@]}" "$DEVCONTAINER" doctor \
  --container "$FAKE_CONTAINER" --format invalid
run_failure env "${COMMON_ENV[@]}" "$DEVCONTAINER" doctor \
  --container "$TEMPORARY_DIRECTORY/missing-container"
touch "$INVALID_UTF8_MARKER"
run_failure env "${COMMON_ENV[@]}" \
  "$DEVCONTAINER" doctor \
  --container "$FAKE_CONTAINER"

run_success env "${COMMON_ENV[@]}" \
  DEVCONTAINER_COMPOSE_PROVIDER=docker \
  DEVCONTAINER_DOCKER_BIN="$FAKE_DOCKER" \
  "$DEVCONTAINER_COMPOSE" version
run_success env "${COMMON_ENV[@]}" \
  DEVCONTAINER_COMPOSE_PROVIDER=container-compose \
  DEVCONTAINER_COMPOSE_BIN="$FAKE_COMPOSE" \
  "$DEVCONTAINER_COMPOSE" --project-name fixture up --detach
run_success env "${COMMON_ENV[@]}" \
  DEVCONTAINER_COMPOSE_PROVIDER=container-compose \
  DEVCONTAINER_COMPOSE_BIN="$FAKE_COMPOSE" \
  "$DEVCONTAINER_COMPOSE" --project-name fixture down
run_failure env "${COMMON_ENV[@]}" \
  DEVCONTAINER_COMPOSE_PROVIDER=container-compose \
  DEVCONTAINER_COMPOSE_BIN="$TEMPORARY_DIRECTORY/missing-compose" \
  "$DEVCONTAINER_COMPOSE" version

if ! compgen -G "$PROFILE_DIRECTORY/devcontainer-cli-*.profraw" >/dev/null; then
  printf 'instrumented CLI smoke produced no coverage profiles\n' >&2
  exit 1
fi
