#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
printf 'container_env=%s\n' "$CONTAINER_VALUE"
printf 'expanded_env=%s\n' "$EXPANDED_VALUE"
printf 'home=%s\n' "$HOME"
printf 'post_create=%s\n' "$(cat /tmp/devcontainer-user-hook)"
printf 'remote_env=%s\n' "$REMOTE_VALUE"
printf 'uid=%s\n' "$(id -u)"
printf 'user=%s\n' "$(id -un)"
