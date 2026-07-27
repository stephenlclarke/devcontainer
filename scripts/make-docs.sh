#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )); then
  printf 'usage: %s OUTPUT_PATH HOSTING_BASE_PATH\n' "$0" >&2
  exit 2
fi

output_path="$1"
hosting_base_path="$2"
scratch_path="${DOCS_SCRATCH_PATH:-.build/docc}"
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source_reference="${DOCS_SOURCE_REFERENCE:-${GITHUB_SHA:-main}}"

swift package \
  --disable-automatic-resolution \
  -Xswiftc -warnings-as-errors \
  --scratch-path "$scratch_path" \
  --allow-writing-to-directory "$output_path" \
  generate-documentation \
  --target DevContainerCore \
  --output-path "$output_path" \
  --disable-indexing \
  --transform-for-static-hosting \
  --hosting-base-path "$hosting_base_path" \
  --source-service github \
  --source-service-base-url \
  "https://github.com/stephenlclarke/devcontainer/blob/$source_reference" \
  --checkout-path "$repository_root"

printf '{}\n' >"$output_path/theme-settings.json"
printf '%s\n' \
  '<!DOCTYPE html>' \
  '<html lang="en-US">' \
  '<head><meta charset="utf-8"><title>devcontainer documentation</title>' \
  '<meta http-equiv="refresh" content="0; url=./documentation/devcontainercore/"></head>' \
  '<body><a href="./documentation/devcontainercore/">Open the documentation.</a></body>' \
  '</html>' >"$output_path/index.html"
