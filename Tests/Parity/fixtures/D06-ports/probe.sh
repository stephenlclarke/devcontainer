#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
inside=$(wget -qO- http://127.0.0.1:8123/)
printf 'forward_metadata=true\n'
printf 'inside_connectivity=%s\n' "$(test "$inside" = devcontainer-port && printf true || printf false)"
