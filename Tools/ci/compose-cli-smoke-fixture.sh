#!/usr/bin/env bash
#
# Provide a deterministic native Compose command boundary for hosted CLI tests.

set -euo pipefail

if (( $# == 3 )) && [[ "$1" == "version" && "$2" == "--format" && "$3" == "json" ]]; then
  printf '%s\n' '{"version":"0.0.0","source":"stephenlclarke/container-compose","commit":"0000000000000000000000000000000000000000","containerDistribution":"apple"}'
  exit 0
fi

if (( $# != 1 )) || [[ "$1" != "version" ]]; then
  printf 'expected: version\n' >&2
  exit 64
fi

printf '%s\n' '{"Version":"fixture"}'
