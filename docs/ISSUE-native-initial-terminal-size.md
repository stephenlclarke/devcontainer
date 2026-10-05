# Native initial TTY dimensions

## Problem

Docker create requests can provide `HostConfig.ConsoleSize` for a TTY container. The stock Apple runtime exposes resize only after init starts, which cannot guarantee the requested size at the guest's first instruction. E15 checks the first `stty size` result before any harness input or resize.

## Candidate implementation

The Docker adapter retains the original dimensions in container metadata and inspection. For a non-default TTY size, the Bazel-installed engine selects a static Linux launcher for the native guest architecture. Its expected SHA-256 is embedded in the signed engine; the launcher is mounted read-only at a fixed guest path. The launcher sets the terminal size before `execve` of the original workload and preserves its arguments, environment, user, and working directory. It does not patch or replace the stock Apple provider.

`[0, 0]` and non-TTY requests retain the native default path. Partial-zero dimensions remain explicit. Restart uses the same identity-verified process configuration rather than applying a later resize.

## Evidence and status

Focused tests cover Docker create/inspect mapping, legacy metadata decoding, launcher identity admission, original-process preservation, mount conflicts, and tamper rejection. The first retained SwiftPM CI run passed 769 tests but failed the unchanged 90% changed-line coverage threshold; focused verifier and runtime-generation guard tests are added, with rerun pending. Root-owned Bazel invocation `4c22f481-5f55-41dc-a5c9-bd86d6f48242` passes the Model, Docker API, Apple runtime and Go launcher targets at the development snapshot. A fresh E15 run must observe the requested dimensions before the first guest output, preserve exit and cleanup assertions, and run against the unchanged fixture. The complete 84-cell release campaign remains required; no release compatibility claim is complete yet.
