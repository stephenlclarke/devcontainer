#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
test "$(cat /usr/local/share/devcontainer-feature-test)" = \
  'devcontainer-feature-test'
