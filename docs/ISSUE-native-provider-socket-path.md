# Issue: native provider socket exceeds Darwin's path limit

## Problem

The finalized-package startup preflight allocated a private SSD directory with the prefix `native-api-preflight-container-compose-`. Under the enrolled `/Volumes/SSD/cf/bazel` root, its Engine provider socket path was 109 UTF-8 bytes, beyond Darwin's 103-byte pathname limit. A preflight that passes under a short root does not prove that the later `native-parity-container-compose-` root will fit; a slightly longer SSD root overflows that layout too.

## Required outcome

Both native lanes must use bounded, lane-scoped scratch names and reject all derived provider socket layouts before Docker or runtime work. Actual allocated roots must be checked again before owner, Keychain, launchd or service changes. An oversized preflight root is retained as an empty, recorded setup failure; an oversized newly allocated parity root is removed only if still empty.

This is a path admission fix, not a successful live parity result. Tracked with [pull request 83](https://github.com/stephenlclarke/devcontainer/pull/83).
