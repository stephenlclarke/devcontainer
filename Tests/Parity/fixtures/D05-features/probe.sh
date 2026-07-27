#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
printf 'feature_git=%s\n' "$(command -v git >/dev/null && printf true || printf false)"
printf 'feature_jq=%s\n' "$(command -v jq >/dev/null && printf true || printf false)"
printf 'lockfile=%s\n' "$(test -s .devcontainer/devcontainer-lock.json && printf true || printf false)"
