#!/usr/bin/env bash
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
# USAGE: version-generator.sh GENERATOR
# Exercise the real compiled generator inside Bazel's sandbox on the SSD.
set -euo pipefail

generator="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
cd "${TEST_TMPDIR:?Bazel must supply owned test storage}"
[[ "$PWD" == /Volumes/SSD/cf/bazel/* ]]
printf 'DEVCONTAINER_VERSION ?= 1.2.3\n' > Makefile
"$generator" Makefile generated.swift unspecified development
grep -q 'static let version = "1.2.3"' generated.swift
grep -q 'static let commit = "unspecified"' generated.swift
grep -q 'static let lane = "development"' generated.swift

# Identical input must not rewrite the file, even outside Bazel's action cache.
/usr/bin/touch -t 202001010000 generated.swift
before="$(/usr/bin/stat -f %m generated.swift)"
"$generator" Makefile generated.swift unspecified development
[[ "$(/usr/bin/stat -f %m generated.swift)" == "$before" ]]

"$generator" Makefile generated.swift 0123456789012345678901234567890123456789 release
grep -q 'static let lane = "release"' generated.swift

# Invalid input must leave the last successful output intact.
cp generated.swift expected.swift
for identity in invalid ABCDEF0123456789012345678901234567890123; do
    if "$generator" Makefile generated.swift "$identity" development; then exit 1; fi
    cmp generated.swift expected.swift
done
if "$generator" Makefile generated.swift unspecified 'bad lane'; then exit 1; fi
if "$generator" missing generated.swift unspecified development; then exit 1; fi
if "$generator" Makefile generated.swift; then exit 1; fi
printf 'DEVCONTAINER_VERSION ?= 1.2.3\nDEVCONTAINER_VERSION ?= 2.3.4\n' > Makefile
if "$generator" Makefile generated.swift unspecified development; then exit 1; fi
printf 'DEVCONTAINER_VERSION ?= 1.2.3-rc1\n' > Makefile
if "$generator" Makefile generated.swift unspecified development; then exit 1; fi
cmp generated.swift expected.swift
printf 'DEVCONTAINER_VERSION ?= 2.0.0\n' > Makefile
"$generator" Makefile nested/generated.swift unspecified development
grep -q 'static let version = "2.0.0"' nested/generated.swift
# Force rename failure and prove its sibling temporary is removed.
mkdir directory-output
if "$generator" Makefile directory-output unspecified development; then exit 1; fi
shopt -s nullglob
temporary_files=(.devcontainer-version-*.tmp)
[[ ${#temporary_files[@]} == 0 ]]
