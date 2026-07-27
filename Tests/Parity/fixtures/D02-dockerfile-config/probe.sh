#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
printf 'build_arg=%s\n' "$(cat /etc/devcontainer-build-arg)"
printf 'build_target=%s\n' "$(cat /etc/devcontainer-build-target)"
printf 'post_create=%s\n' "$(cat /tmp/devcontainer-post-create)"
printf 'workspace=%s\n' "$PWD"
