#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
printf 'host_hook=%s\n' "$(cat .lifecycle-host)"
printf 'order=%s\n' "$(paste -sd, /tmp/devcontainer-lifecycle)"
