#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
install -d /usr/local/share
printf '%s\n' 'devcontainer-feature-test' \
  >/usr/local/share/devcontainer-feature-test
