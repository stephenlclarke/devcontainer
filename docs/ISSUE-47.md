# Issue 47: build Dev Containers from one coherent dependency graph

## State

`Blocked` pending a committed, reproducible dependency composition. This issue
does not report a C04 candidate runtime result.

## Problem

Signed local `main` at `3bd1e6230f7bbd19cc8491d233aa305cb7cecc31` cannot build
`devcontainer-engine` from its declared dependency graph. The package pins
Engine API `0.3.3`, but the current logging-handoff sources require the
`ProviderHandoffPortableLogging*` types absent from that release.

The focused command is:

```console
swift build --disable-automatic-resolution --product devcontainer-engine
```

A marker-owned, disposable local-path graph was used only to classify the
failure. It selects Engine API
`4949e743675f00ec102f7acacdb4e990409e383f`, Container
`a661e67c8e7713483eb448493c7b4a35f346d9b3`, and Containerization
`cfb00bbf3523079fe2ab9fb6b8e9b3504eff77e5`. The logging types then resolve,
but compilation stops at
`Sources/DevContainerAppleRuntime/AppleContainerRuntimeSupport.swift:65`:

```text
value of optional type 'CIDRv4?' must be unwrapped to refer to member
'address' of wrapped base type 'CIDRv4'
```

The source-matched Container API intentionally represents IPv4 as optional for
IPv6-only network attachments. Dev Containers still assumes an IPv4 address is
always present.

## Expected behavior

- One committed `Package.swift`/`Package.resolved` graph resolves the logging
  handoff API and Container attachment API together.
- The Apple runtime projects an attachment without IPv4 safely, preserving the
  supported address information rather than failing to compile or inventing an
  address.
- Both `devcontainer-engine` and `devcontainer-compose` build from that graph
  without an editable-package overlay.

## Acceptance evidence

- Focused tests cover IPv4-present and IPv4-absent attachment projection.
- The two product builds pass under the committed graph.
- A fresh, marker-protected Container Compose C04 candidate lane records one
  exact source/dependency/binary/guest/root fingerprint and is reconciled with
  [container-compose issue #184](https://github.com/stephenlclarke/container-compose/issues/184).

## Tracking

- GitHub issue: [#47](https://github.com/stephenlclarke/devcontainer/issues/47)
- Local Compose handoff: `C04-CANDIDATE-LIFECYCLE-01` records the exact
  candidate blocker and must remain aligned with this issue.

The local-path overlay is diagnostic evidence only. It must not be committed,
published, or treated as a release composition.
