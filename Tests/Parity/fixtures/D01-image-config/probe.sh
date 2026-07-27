#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
printf 'environment=%s\n' "${DEVCONTAINER_PARITY-}"
printf 'workspace=%s\n' "$PWD"
printf 'post_create=%s\n' "$(cat /tmp/devcontainer-post-create)"
printf 'uid=%s\n' "$(id -u)"
