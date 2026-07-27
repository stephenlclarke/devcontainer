#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
printf 'create_count=%s\n' "$(cat /cache/create-count)"
printf 'start_count=%s\n' "$(cat /cache/start-count)"
