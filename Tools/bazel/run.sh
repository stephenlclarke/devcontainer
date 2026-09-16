#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: run.sh configure|test-tools | build|test|coverage|query|cquery|aquery|info|shutdown [ARGS...]
# Enrol /Volumes/SSD once with configure, then use the pinned native Bazel targets.
# Every tool download, cache, JVM temporary file and test output stays on that disk.
# CONTAINER_FAMILY_SSD_UUID may supply an explicit expected UUID instead of enrolment.
set -euo pipefail

readonly SELF_PATH="${BASH_SOURCE[0]}"
readonly SCRIPT_NAME="${SELF_PATH##*/}"
readonly BAZEL_VERSION="8.8.0"
readonly BAZEL_SHA256="f0ac192aba2ccaa373cdfd527d4c407cc492c1296a2f11a4b67563e4d5aa9acb"
readonly SSD_VOLUME="/Volumes/SSD"
readonly SSD_ROOT="$SSD_VOLUME/cf/bazel"

# Report an actionable failure without exposing caller environment values.
error() {
    printf '%s: %s\n' "$SCRIPT_NAME" "$*" >&2
    return 2
}

# Explain the intentionally limited migration interface.
usage() {
    printf 'Usage: %s configure|test-tools | build|test|coverage|query|cquery|aquery|info|shutdown [ARGS...]\n' "$SCRIPT_NAME"
    printf 'First run configure to enrol /Volumes/SSD, or set CONTAINER_FAMILY_SSD_UUID.\n'
    printf 'Example: %s coverage //:bazel_qualification\n' "$SCRIPT_NAME"
}

# Reject alias mounts and disk replacement, including a missing expected identity.
validate_volume() {
    local expected="$1" actual="$2" mount="$3" internal="$4"
    [[ "$expected" =~ ^[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}$ ]] || {
        error 'SSD is not enrolled; run configure first.'; return 2;
    }
    [[ "$expected" == "$actual" && "$mount" == "$SSD_VOLUME" && "$internal" == false ]] || {
        error 'SSD identity or mount does not match; refusing internal-disk fallback.'; return 2;
    }
}

# Create owned paths without following a pre-existing symlink in any component.
ensure_directory() {
    local requested="$1" part current="" parts=()
    [[ "$requested" == /* && "$requested" != *$'\n'* ]] || { error 'Expected an absolute directory.'; return 2; }
    IFS=/ read -r -a parts <<< "$requested"
    for part in "${parts[@]}"; do
        [[ -n "$part" ]] || continue
        [[ "$part" != . && "$part" != .. ]] || { error 'Relative path components are forbidden.'; return 2; }
        current="$current/$part"
        [[ ! -L "$current" ]] || { error "Refusing symlink: $current"; return 2; }
        if [[ -e "$current" ]]; then
            [[ -d "$current" ]] || { error "Not a directory: $current"; return 2; }
        else
            mkdir "$current" || return
        fi
    done
}

# Keep storage and evidence flags under launcher authority, not caller overrides.
validate_arguments() {
    local argument
    for argument in "$@"; do
        case "$argument" in
            --output_user_root*|--output_base*|--install_base*|--bazelrc*|--host_jvm_args*|--disk_cache*|--repository_cache*|--repo_contents_cache*|--test_tmpdir*|--sandbox_base*|--sandbox_writable_path*|--build_event_*|--profile*|--execution_log_*|--experimental_execution_log*|--remote_cache*|--remote_executor*|--symlink_prefix*|--action_env*|--test_env*|--host_action_env*|--repo_env*|--run_under*)
                error "Storage/environment override is not supported: ${argument%%=*}"; return 2 ;;
        esac
    done
}

# Authenticate bytes before running downloaded tooling, including cache hits.
verify_digest() {
    local file="$1" expected="$2" actual
    [[ -f "$file" && ! -L "$file" ]] || return 1
    actual="$(/usr/bin/shasum -a 256 "$file")" || return
    [[ "${actual%% *}" == "$expected" ]]
}

# Resolve and enrol storage before allowing build or test tooling to start.
main() {
    local command="${1:---help}" repo config_root config metadata expected actual mount internal
    local executable partial invocation bazel_args=() command_args=() pinned_version part
    export PATH=/usr/bin:/bin:/usr/sbin:/sbin
    case "$command" in
        -h|--help) usage; return 0 ;;
        configure|test-tools|build|test|coverage|query|cquery|aquery|info|shutdown) shift ;;
        *) usage >&2; error 'Unsupported command.'; return 2 ;;
    esac
    [[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { error 'This qualification launcher requires Apple silicon macOS.'; return 2; }
    validate_arguments "$@" || return
    repo="$(cd "$(dirname "$SELF_PATH")/../.." && pwd -P)"
    read -r pinned_version < "$repo/.bazelversion"
    [[ "$pinned_version" == "$BAZEL_VERSION" ]] || { error 'Bazel version and verified bootstrap checksum disagree.'; return 2; }
    metadata="$(/usr/sbin/diskutil info -plist "$SSD_VOLUME")" || { error 'SSD is unavailable.'; return 2; }
    actual="$(printf '%s' "$metadata" | /usr/bin/plutil -extract VolumeUUID raw -)"
    mount="$(printf '%s' "$metadata" | /usr/bin/plutil -extract MountPoint raw -)"
    internal="$(printf '%s' "$metadata" | /usr/bin/plutil -extract Internal raw -)"
    config_root="$HOME/Library/Application Support/ContainerFamily/retained/workflow"
    config="$config_root/ssd-volume.uuid"
    [[ ! -L "$config" ]] || { error 'Refusing symlinked SSD enrolment.'; return 2; }
    if [[ "$command" == configure ]]; then
        [[ $# == 0 ]] || { error 'configure accepts no arguments.'; return 2; }
        validate_volume "$actual" "$actual" "$mount" "$internal" || return
        umask 077
        ensure_directory "$config_root" || return
        [[ "$(/usr/bin/stat -f %d "$config_root")" == "$(/usr/bin/stat -f %d "$HOME")" ]] || { error 'Retained configuration must be on internal home storage.'; return 2; }
        if [[ -e "$config" ]]; then
            read -r expected < "$config"
            validate_volume "$expected" "$actual" "$mount" "$internal" || return
        else
            printf '%s\n' "$actual" > "$config"
        fi
        printf 'Enrolled SSD %s. No build started.\n' "$actual"
        return
    fi
    expected="${CONTAINER_FAMILY_SSD_UUID:-}"
    if [[ -z "$expected" && -f "$config" ]]; then read -r expected < "$config"; fi
    validate_volume "$expected" "$actual" "$mount" "$internal" || return
    umask 077
    ensure_directory "$SSD_ROOT" || return
    for part in bootstrap tmp output cache repository invocations; do
        ensure_directory "$SSD_ROOT/$part" || return
    done
    export TMPDIR="$SSD_ROOT/tmp" TMP="$SSD_ROOT/tmp" TEMP="$SSD_ROOT/tmp"
    if [[ "$command" == test-tools ]]; then
        [[ $# == 0 ]] || { error 'test-tools accepts no arguments.'; return 2; }
        export PYTHONDONTWRITEBYTECODE=1
        exec /usr/bin/python3 -m unittest discover -s "$repo/Tools/bazel" -p 'test_*.py'
    fi
    export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
    [[ -d "$DEVELOPER_DIR/Platforms/MacOSX.platform" ]] || {
        error 'DEVELOPER_DIR must select full Xcode, not standalone Command Line Tools.'; return 2;
    }
    executable="$SSD_ROOT/bootstrap/bazel-$BAZEL_VERSION-darwin-arm64"
    if ! verify_digest "$executable" "$BAZEL_SHA256"; then
        [[ ! -e "$executable" && ! -L "$executable" ]] || { error 'Cached Bazel failed verification; preserve it for diagnosis.'; return 2; }
        partial="$(mktemp "$SSD_ROOT/tmp/bazel.XXXXXX")"
        if ! /usr/bin/curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
            -o "$partial" "https://releases.bazel.build/$BAZEL_VERSION/release/bazel-$BAZEL_VERSION-darwin-arm64" \
            || ! verify_digest "$partial" "$BAZEL_SHA256"; then
            rm -f "$partial"
            error 'Bazel download failed verification.'; return 2
        fi
        chmod 700 "$partial"
        mv "$partial" "$executable"
    fi
    bazel_args=(--nosystem_rc --nohome_rc --noworkspace_rc "--bazelrc=$repo/.bazelrc"
        "--output_user_root=$SSD_ROOT/output" "--host_jvm_args=-Djava.io.tmpdir=$TMPDIR")
    cd "$repo"
    case "$command" in
        shutdown) [[ $# == 0 ]] || { error 'shutdown accepts no arguments.'; return 2; }
            exec "$executable" "${bazel_args[@]}" shutdown ;;
        *) invocation="$(mktemp -d "$SSD_ROOT/invocations/run.XXXXXX")"
            printf 'Bazel evidence: %s\n' "$invocation" >&2
            case "$command" in
                build|test|coverage) command_args+=("--disk_cache=$SSD_ROOT/cache") ;;
            esac
            case "$command" in
                test|coverage) command_args+=("--test_tmpdir=$SSD_ROOT/tmp/tests") ;;
            esac
            "$executable" "${bazel_args[@]}" "$command" "$@" \
                "${command_args[@]}" "--repository_cache=$SSD_ROOT/repository" \
                "--build_event_json_file=$invocation/events.json" || return
            if [[ "$command" == coverage ]]; then
                for part in "$@"; do
                    if [[ "$part" == //:bazel_qualification ]]; then
                        PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 "$repo/Tools/bazel/check_evidence.py" \
                            "$invocation/events.json" > "$invocation/qualification.json" || return
                        printf 'Validated qualification evidence: %s/qualification.json\n' "$invocation"
                    fi
                done
            fi ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
