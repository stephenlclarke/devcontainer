# Issue: compile and bound the Docker frontend with both dependency profiles

## Motivation

The exact-head native Docker-client target and hosted source checks fail because Enhanced Container Engine API `48e44d74d738ca3d24351ba02c4869be1a3e6998` does not expose the duplex connection and `openDuplex` declarations used by the Docker frontend. Stock API `40436017e1e93012b8dab7cfc3c79783538065c3` provides them, alongside cancellation-aware absolute deadlines. Its broader dependency revision contains unrelated gateway/server changes; replacing the enhanced pin would invalidate already qualified dependency recipes.

## Required behaviour

Compile the first-party Docker frontend against both admitted profiles without changing dependency pins or release recipes. Real Unix-socket requests, streams and upgraded sessions must retain bounded responses, socket ownership checks, prompt cancellation and absolute deadlines. Concurrent input/output must preserve head-adjacent bytes and stdin half-close. Ordinary requests also need cancellation-aware transport so build interruption and terminal-status cleanup can join promptly.

Exec must also preserve Docker's remote terminal allocation: `-t`/`--tty` selects a TTY at exec creation and start, terminal output is forwarded as unframed combined stdout, and `-i` independently controls stdin attachment. This is remote PTY transport support; it does not imply host-side raw terminal mode or resize forwarding.

## Scope and validation

Adapt the existing reviewed bounded transport under internal first-party names, sharing HTTP wire/response and ordinary transport error types. Preserve all lower-layer releases and their qualification evidence. Run the complete maintained native Docker-client test target with both stock and enhanced prebuilt Engine API inputs, including the existing executable signal/build and real exec socket contracts. Final source quality, package, live parity and publication remain separate gates.

Linked implementation: [transport handoff](PR-docker-frontend-transport.md). Review: [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).
