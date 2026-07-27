#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu

phase="${1:-}"
state="/workspaces/vscode-parity/.devcontainer-evidence"
mkdir -p "$state"

case "$phase" in
  create)
    count="$(cat "$state/post-create-count.txt" 2>/dev/null || printf '0')"
    count="$((count + 1))"
    printf '%s\n' "$count" >"$state/post-create-count.txt"
    hostname >"$state/guest-hostname.txt"
    ;;
  attach)
    count="$(cat "$state/post-attach-count.txt" 2>/dev/null || printf '0')"
    count="$((count + 1))"
    printf '%s\n' "$count" >"$state/post-attach-count.txt"
    ;;
  *)
    printf 'usage: %s create|attach\n' "$0" >&2
    exit 2
    ;;
esac
