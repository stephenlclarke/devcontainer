#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
metadata="${DEVCONTAINER_NATIVE_COMPOSE_METADATA:-${repository_root}/Tools/release/native-compose.json}"
output="${1:?usage: build-native-compose.sh OUTPUT_DIRECTORY}"

if [[ "$(uname -m)" != "arm64" ]]; then
  printf 'native Compose release packages require arm64\n' >&2
  exit 1
fi
if [[ -e "$output" ]]; then
  printf 'native Compose output already exists: %s\n' "$output" >&2
  exit 1
fi

compose_repository="$(jq -er '.repository' "$metadata")"
compose_commit="$(jq -er '.commit | select(test("^[0-9a-f]{40}$"))' "$metadata")"
compose_version="$(jq -er '.version' "$metadata")"
go_version="$(jq -er '.goVersion' "$metadata")"
container_version="$(jq -er '.appleContainerVersion' "$metadata")"
container_revision="$(jq -er '.appleContainerRevision' "$metadata")"
containerization_version="$(jq -er '.appleContainerizationVersion' "$metadata")"
containerization_revision="$(jq -er '.appleContainerizationRevision' "$metadata")"
if [[ "$(go env GOVERSION)" != "go${go_version}" ]]; then
  printf 'native Compose requires Go %s; found %s\n' \
    "$go_version" "$(go env GOVERSION)" >&2
  exit 1
fi

temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/devcontainer-native-compose.XXXXXX")"
cleanup() {
  case "$temporary_root" in
    "${TMPDIR:-/tmp}"/devcontainer-native-compose.*) rm -rf "$temporary_root" ;;
    *) printf 'refusing to remove unexpected temporary path: %s\n' "$temporary_root" >&2 ;;
  esac
}
trap cleanup EXIT
source_root="$temporary_root/source"
stage="$temporary_root/stage"
go_vendor="$source_root/Tools/compose-normalizer/vendor"

git init -q "$source_root"
git -C "$source_root" remote add origin "$compose_repository"
git -C "$source_root" fetch -q --depth 1 origin "$compose_commit"
git -C "$source_root" checkout -q --detach FETCH_HEAD
[[ "$(git -C "$source_root" rev-parse HEAD)" == "$compose_commit" ]]
[[ -z "$(git -C "$source_root" status --short)" ]]

cp "$source_root/Package.stock.resolved" "$source_root/Package.resolved"
CONTAINER_COMPOSE_BUILD_PROFILE=stock swift build \
  --package-path "$source_root" \
  --disable-automatic-resolution \
  -Xswiftc -warnings-as-errors \
  -c release \
  --product compose

mkdir -p "$stage/bin" "$stage/resources"
install -m 0755 "$source_root/.build/release/compose" "$stage/bin/compose"
(
  cd "$source_root/Tools/compose-normalizer"
  GOTOOLCHAIN=local go mod vendor
  CGO_ENABLED=0 GOTOOLCHAIN=local go build -mod=vendor -trimpath -ldflags '-s -w' \
    -o "$stage/resources/compose-normalizer" .
  mkdir -p "$stage/resources/volume-initializer"
  CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOTOOLCHAIN=local \
    go build -mod=vendor -trimpath -ldflags '-s -w' \
    -o "$stage/resources/volume-initializer/compose-volume-initializer-linux-arm64" \
    ./cmd/volume-initializer
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOTOOLCHAIN=local \
    go build -mod=vendor -trimpath -ldflags '-s -w' \
    -o "$stage/resources/volume-initializer/compose-volume-initializer-linux-amd64" \
    ./cmd/volume-initializer
)
[[ -x "$stage/resources/volume-initializer/compose-volume-initializer-linux-arm64" ]]
[[ -x "$stage/resources/volume-initializer/compose-volume-initializer-linux-amd64" ]]
install -m 0644 "$source_root/LICENSE" "$stage/LICENSE"
install -m 0644 "$source_root/config.toml" "$stage/config.toml"
install -m 0644 "$source_root/Package.stock.resolved" \
  "$stage/resources/Package.resolved"
install -m 0644 "$go_vendor/modules.txt" "$stage/resources/go-modules.txt"

source_date_epoch="$(git -C "$source_root" show -s --format=%ct HEAD)"
go_root="$(go env GOROOT)"
python3 "$repository_root/Tools/release/write-native-compose-legal.py" \
  --version "$compose_version" \
  --commit "$compose_commit" \
  --source-date-epoch "$source_date_epoch" \
  --resolved "$source_root/Package.stock.resolved" \
  --license-manifest "$repository_root/Tools/release/dependency-licenses.stock.json" \
  --checkouts "$source_root/.build/checkouts" \
  --go-vendor "$go_vendor" \
  --go-version "$go_version" \
  --go-license "$go_root/LICENSE" \
  --notices-output "$stage/THIRD-PARTY-NOTICES.txt" \
  --sbom-output "$stage/resources/container-compose.spdx.json"

compose_go_version="$(
  python3 "$source_root/Tools/release/go-module-version.py" \
    --go-mod "$source_root/Tools/compose-normalizer/go.mod" \
    github.com/compose-spec/compose-go/v2
)"
python3 "$source_root/Tools/release/write-build-info.py" \
  --output "$stage/resources/build-info.json" \
  --version "$compose_version" \
  --source stephenlclarke/container-compose \
  --branch detached \
  --lane bundled-stock \
  --commit "$compose_commit" \
  --build-type release \
  --container-source apple/container \
  --container-ref "$container_revision" \
  --containerization-source apple/containerization \
  --containerization-ref "$containerization_revision" \
  --compose-go-version "$compose_go_version" \
  --runtime-profile stock

jq -e \
  --arg compose_commit "$compose_commit" \
  --arg compose_version "$compose_version" \
  --arg container_revision "$container_revision" \
  --arg containerization_revision "$containerization_revision" \
  '(.commit == $compose_commit) and
   (.version == $compose_version) and
   (.containerRef == $container_revision) and
   (.containerizationRef == $containerization_revision) and
   (.runtimeCapabilities == [])' \
  "$stage/resources/build-info.json" >/dev/null
[[ "$container_version" == "1.4.1" ]]
[[ "$containerization_version" == "0.45.0" ]]

mkdir -p "$(dirname "$output")"
mv "$stage" "$output"
printf '%s\n' "$output"
