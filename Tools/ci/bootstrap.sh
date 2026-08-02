#!/usr/bin/env bash
set -euo pipefail

required=(swift python3 ruby make git)
optional=(markdownlint swiftlint swiftformat shellcheck jq vhs)

for tool in "${required[@]}"; do
  command -v "$tool" >/dev/null || {
    printf 'required tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

for tool in "${optional[@]}"; do
  command -v "$tool" >/dev/null || printf 'optional quality tool is missing: %s\n' "$tool" >&2
done

swift --version
python3 --version
ruby --version
git --version
