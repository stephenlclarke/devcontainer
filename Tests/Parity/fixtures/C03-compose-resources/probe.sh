#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
printf 'volume-data' > /cache/value
printf 'env_file=%s\n' "$PARITY_ENV_FILE"
printf 'named_volume=%s\n' "$(cat /cache/value)"
printf 'network_alias=%s\n' "$(ping -c 1 parity-app >/dev/null && printf true || printf false)"
printf 'network_peer=%s\n' "$(ping -c 1 peer >/dev/null && printf true || printf false)"
