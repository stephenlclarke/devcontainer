#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
printf 'compose_env=%s\n' "$COMPOSE_VALUE"
printf 'post_create=%s\n' "$(cat /tmp/devcontainer-compose-hook)"
printf 'workspace=%s\n' "$PWD"
