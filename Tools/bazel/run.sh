#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: run.sh [--workspace ABSOLUTE_REPOSITORY] configure|test-tools|cleanup [--days N] [--apply]|restore-candidate ID|coverage-report ID|build-timings ID [--baseline ID]|acquire-releases LOCK [--offline]|prepare-releases LOCK [--offline] | build|test|coverage|query|cquery|aquery|info|shutdown [ARGS...]
# Enrol /Volumes/SSD once with configure, then use the pinned native Bazel targets.
# Every tool download, cache, JVM temporary file and test output stays on that disk.
# CONTAINER_FAMILY_SSD_UUID may supply an explicit expected UUID instead of enrolment.
set -euo pipefail

TOOL_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly TOOL_DIRECTORY
readonly SELF_PATH="$TOOL_DIRECTORY/${BASH_SOURCE[0]##*/}"
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
    printf 'Optional prefix: --workspace ABSOLUTE_REPOSITORY (reuse the pinned family tools).\n'
    printf 'Usage: %s configure|test-tools|restore-candidate ID|coverage-report ID|acquire-releases LOCK [--offline] | build|test|coverage|query|cquery|aquery|info|shutdown [ARGS...]\n' "$SCRIPT_NAME"
    printf '       %s cleanup [--days N] [--apply] (default: report only, 14 days)\n' "$SCRIPT_NAME"
    printf '       %s build-timings ID [--baseline ID] (retained measured durations)\n' "$SCRIPT_NAME"
    printf '       %s prepare-releases LOCK [--offline] (unpack releases on SSD, never install or build)\n' "$SCRIPT_NAME"
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
            --define=runtime_profile*|runtime_profile=*)
                error 'Select the dependency and compile profile together with --config=stock or --config=enhanced.'; return 2 ;;
            --override_module*|--override_repository*|--inject_repository*|--lockfile_mode*|--registry*|--module_mirrors*|--experimental_downloader_config*|--enable_bzlmod*|--noenable_bzlmod*|--enable_workspace*|--noenable_workspace*)
                error "Dependency override is not supported: ${argument%%=*}"; return 2 ;;
            --client_env*|--output_user_root*|--output_base*|--install_base*|--bazelrc*|--host_jvm_args*|--disk_cache*|--repository_cache*|--repo_contents_cache*|--test_tmpdir*|--sandbox_base*|--sandbox_writable_path*|--build_event_*|--profile*|--execution_log_*|--experimental_execution_log*|--remote_cache*|--remote_executor*|--symlink_prefix*|--action_env*|--test_env*|--host_action_env*|--repo_env*|--run_under*)
                error "Storage/environment override is not supported: ${argument%%=*}"; return 2 ;;
            *) : ;; # Other target and diagnostic options remain Bazel's responsibility.
        esac
    done
}

# Resolve only the two coherent dependency profiles, rejecting ambiguous requests.
runtime_profile() {
    local argument profile=enhanced selected=""
    for argument in "$@"; do
        case "$argument" in
            --config=stock|--config=enhanced)
                profile="${argument#--config=}"
                [[ -z "$selected" || "$selected" == "$profile" ]] || {
                    error 'Select one runtime profile per invocation.'; return 2;
                }
                selected="$profile" ;;
            --config) error 'Use --config=NAME, not split config arguments.'; return 2 ;;
            *) : ;; # Non-profile options do not affect this selection.
        esac
    done
    printf '%s\n' "$profile"
}

# Emit non-profile arguments losslessly; the selected profile is added once.
execution_arguments() {
    local argument
    for argument in "$@"; do
        case "$argument" in
            --config=stock|--config=enhanced) : ;; # Normalized by runtime_profile.
            *) printf '%s\0' "$argument" ;;
        esac
    done
}

# Bazel accepts all three spellings for a root-package target.
is_qualification_label() {
    case "$1" in
        bazel_qualification|:bazel_qualification|//:bazel_qualification) return 0 ;;
        *) return 1 ;;
    esac
}

# Never let shell credentials/startup hooks enter Bazel or its event stream.
clean_environment() {
    local host_integration="${DEVCONTAINER_HOST_INTEGRATION:-0}"
    [[ "$host_integration" == 0 || "$host_integration" == 1 ]] || { error 'Host integration opt-in must be 0 or 1.'; return 2; }
    /usr/bin/env -i HOME="$HOME" USER="$(/usr/bin/id -un)" LOGNAME="$(/usr/bin/id -un)" \
        PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
        TMPDIR="$SSD_ROOT/tmp" TMP="$SSD_ROOT/tmp" TEMP="$SSD_ROOT/tmp" \
        DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}" \
        PYTHONDONTWRITEBYTECODE=1 DEVCONTAINER_HOST_INTEGRATION="$host_integration" "$@"
}

# Assemble a nonempty argument array, including on macOS Bash 3.2 query paths.
run_bazel() {
    local repo="$1" command="$2" invocation="$3" executable="$4" profile="$5"
    shift 5
    local part bazel_args=()
    bazel_args=(--nosystem_rc --nohome_rc --noworkspace_rc "--bazelrc=$repo/.bazelrc"
        "--output_user_root=$SSD_ROOT/output" "--host_jvm_args=-Djava.io.tmpdir=$TMPDIR"
        "$command" "--config=$profile")
    while IFS= read -r -d '' part; do bazel_args+=("$part"); done < <(execution_arguments "$@")
    case "$command" in
        build|test|coverage) bazel_args+=("--disk_cache=$SSD_ROOT/cache") ;;
        *) : ;; # Query commands have no action cache.
    esac
    case "$command" in
        test|coverage) bazel_args+=("--test_tmpdir=$SSD_ROOT/t") ;;
        *) : ;; # Only test executions need test scratch.
    esac
    local measurement=()
    case "$command" in
        build|test|coverage) measurement=(/usr/bin/python3 "$TOOL_DIRECTORY/build_timing.py" measure "$invocation/timing.json" "$profile" --) ;;
        *) : ;; # Diagnostics do not create build timing observations.
    esac
    clean_environment ${measurement[@]+"${measurement[@]}"} "$executable" "${bazel_args[@]}" \
        "--repository_cache=$SSD_ROOT/repository" "--build_event_json_file=$invocation/events.json"
}

# Run Bazel once and seal reports while the workspace output lease is held.
execute_invocation() {
    local repo="$1" command="$2" invocation="$3" executable="$4" profile="$5"
    shift 5
    local part suite="" status=0 validation=0
    # Bazel info emits no BEP. It is a leased diagnostic, not retained build proof.
    if [[ "$command" == info ]]; then
        run_bazel "$repo" "$command" "$invocation" "$executable" "$profile" "$@"
        return
    fi
    /usr/bin/python3 "$TOOL_DIRECTORY/input_identity.py" "$repo" --tooling "$TOOL_DIRECTORY" > "$invocation/inputs-before.json" || return
    run_bazel "$repo" "$command" "$invocation" "$executable" "$profile" "$@" || status=$?
    /usr/bin/python3 "$TOOL_DIRECTORY/input_identity.py" "$repo" --tooling "$TOOL_DIRECTORY" \
        --verify "$invocation/inputs-before.json" > "$invocation/inputs-after.json" || validation=$?
    if [[ "$command" == coverage && "$status" == 0 && "$validation" == 0 ]]; then
        for part in "$@"; do
            if is_qualification_label "$part"; then suite=qualification; fi
            case "$part" in
                source_tests|:source_tests|//:source_tests|unit|:unit|//:unit) suite=source ;;
                *) : ;; # Focused targets retain raw evidence without a whole-suite claim.
            esac
        done
        if [[ -n "$suite" ]]; then
            local report=qualification.json
            [[ "$suite" != source ]] || report=source-tests.json
            /usr/bin/python3 "$repo/Tools/bazel/check_evidence.py" --suite "$suite" --profile "$profile" \
                "$invocation/events.json" > "$invocation/$report" || validation=$?
        fi
    fi
    printf '{"bazel_exit_code":%d,"validation_exit_code":%d,"suite":"%s"}\n' \
        "$status" "$validation" "$suite" > "$invocation/outcome.json"
    # Failed completed runs are retained too; an interrupted/incomplete BEP is
    # deliberately not sealed as a result. Nothing schedules a retry here.
    /usr/bin/python3 "$TOOL_DIRECTORY/retain_evidence.py" "$invocation/events.json" || {
        error "Evidence retention failed; preserve $invocation before another invocation."; return 2;
    }
    [[ "$status" == 0 ]] || return "$status"
    return "$validation"
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
    local workspace=""
    if [[ "${1:-}" == --workspace ]]; then
        [[ $# -ge 3 && "$2" == /* && -d "$2" ]] || { error '--workspace requires an absolute repository and a command.'; return 2; }
        workspace="$(cd "$2" && pwd -P)"
        shift 2
    fi
    local command="${1:---help}" repo config_root config metadata expected actual mount internal
    local executable partial invocation bazel_args=() pinned_version part profile workspace_key
    local first_argument second_argument
    export PATH=/usr/bin:/bin:/usr/sbin:/sbin
    case "$command" in
        -h|--help) usage; return 0 ;;
        configure|test-tools|cleanup|restore-candidate|coverage-report|build-timings|acquire-releases|prepare-releases|build|test|coverage|query|cquery|aquery|info|shutdown) shift ;;
        *) usage >&2; error 'Unsupported command.'; return 2 ;;
    esac
    [[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { error 'This qualification launcher requires Apple silicon macOS.'; return 2; }
    validate_arguments "$@" || return
    profile="$(runtime_profile "$@")" || return
    first_argument="${1:-}"
    second_argument="${2:-}"
    repo="${workspace:-$(cd "$TOOL_DIRECTORY/../.." && pwd -P)}"
    [[ -f "$repo/MODULE.bazel" && -f "$repo/.bazelrc" ]] || { error 'Workspace must declare its Bazel module and configuration.'; return 2; }
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
    for part in bootstrap tmp t output cache repository invocations locks; do
        ensure_directory "$SSD_ROOT/$part" || return
    done
    export TMPDIR="$SSD_ROOT/tmp" TMP="$SSD_ROOT/tmp" TEMP="$SSD_ROOT/tmp" PYTHONDONTWRITEBYTECODE=1
    ensure_directory "$config_root" || return
    if [[ "$command" == cleanup ]]; then
        clean_environment /usr/bin/python3 "$TOOL_DIRECTORY/hygiene.py" cleanup "$@"
        return
    fi
    if [[ "$command" == acquire-releases || "$command" == prepare-releases ]]; then
        [[ $# == 1 || ( $# == 2 && "$second_argument" == --offline ) ]] || { error "$command requires LOCK [--offline]."; return 2; }
        local release_helper=release_inputs.py
        if [[ "$command" == prepare-releases ]]; then
            release_helper=prepare_releases.py
            ensure_directory "$SSD_ROOT/prepared-releases" || return
            ensure_directory "$config_root/prepared-receipts" || return
        fi
        clean_environment /usr/bin/lockf -k -t 300 "$SSD_ROOT/locks/reference-store.lock" \
            /usr/bin/python3 "$TOOL_DIRECTORY/$release_helper" "$@"
        return
    fi
    if [[ "$command" == restore-candidate ]]; then
        [[ $# == 1 ]] || { error 'restore-candidate requires one retained invocation ID.'; return 2; }
        clean_environment /usr/bin/python3 "$TOOL_DIRECTORY/retain_evidence.py" --restore-candidate "$first_argument"
        return
    fi
    if [[ "$command" == coverage-report ]]; then
        [[ $# == 1 ]] || { error 'coverage-report requires one retained invocation ID.'; return 2; }
        clean_environment /usr/bin/python3 "$TOOL_DIRECTORY/coverage_report.py" "$first_argument"
        return
    fi
    if [[ "$command" == build-timings ]]; then
        [[ $# == 1 || ( $# == 3 && "$second_argument" == --baseline ) ]] || { error 'build-timings requires ID [--baseline ID].'; return 2; }
        clean_environment /usr/bin/python3 "$TOOL_DIRECTORY/build_timing.py" report "$@"
        return
    fi
    if [[ "$command" == test-tools ]]; then
        [[ $# == 0 ]] || { error 'test-tools accepts no arguments.'; return 2; }
        export PYTHONDONTWRITEBYTECODE=1
        exec /usr/bin/python3 -m unittest discover -s "$TOOL_DIRECTORY" -p 'test_*.py'
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
            clean_environment "$executable" "${bazel_args[@]}" shutdown ;;
        *) if [[ "$command" == info ]]; then
                # info writes no BEP, so do not leave an empty run directory.
                invocation="$SSD_ROOT/invocations/diagnostic-info"
            else
                invocation="$(mktemp -d "$SSD_ROOT/invocations/run.XXXXXX")"
                clean_environment /usr/bin/python3 "$TOOL_DIRECTORY/hygiene.py" register "$invocation" "$repo" || return
                printf 'Bazel evidence: %s\n' "$invocation" >&2
            fi
            workspace_key="$(printf '%s' "$repo" | /usr/bin/shasum -a 256)"
            # The child expands its own positional arguments under the lease.
            # shellcheck disable=SC2016
            clean_environment /usr/bin/lockf -k -t 300 "$SSD_ROOT/locks/${workspace_key%% *}.lock" \
                /bin/bash -c 'source "$1"; shift; execute_invocation "$@"' _ "$SELF_PATH" \
                "$repo" "$command" "$invocation" "$executable" "$profile" "$@" ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
