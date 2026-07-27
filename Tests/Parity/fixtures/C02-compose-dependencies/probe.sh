#!/bin/sh
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

set -eu
printf 'dependency_dns=%s\n' "$(ping -c 1 database >/dev/null && printf true || printf false)"
printf 'dependency_health=%s\n' "$(wget -qO- http://database:8080/ready)"
printf 'run_service=%s\n' "$(ping -c 1 helper >/dev/null && printf true || printf false)"
