#!/usr/bin/env bash
#
# Provide a deterministic native Compose command boundary for hosted CLI tests.

set -euo pipefail

if (( $# != 2 )) || [[ "$1" != "compose" || "$2" != "version" ]]; then
  printf 'expected: compose version\n' >&2
  exit 64
fi

printf '%s\n' '{"Version":"fixture"}'
