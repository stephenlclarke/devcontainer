# Docker CLI image pull is missing from the local Engine adapter

## Problem

The signed `devcontainer-docker` adapter rejects `docker pull`, although the Docker Engine API router already implements `POST /images/create` and streams image progress. The pinned Dev Containers CLI checks for a missing image and pulls digest-pinned references during `up`, so the unsupported command makes native lifecycle fixtures fail before container creation.

## Expected behavior

`devcontainer-docker pull IMAGE` should send the exact image reference to the selected local Engine API endpoint, relay bounded progress to stdout, and return failure when the Engine rejects the request or reports an error in its stream. Quiet mode should consume and validate the stream without printing progress. Unsupported flags and ambiguous references should fail during argument parsing.

## Scope

Implement this command through the fixed Engine image-create path, held as a private protocol constant, with its `fromImage` query built separately. Keep the local Unix socket routing and existing Engine API lifecycle behavior unchanged; the endpoint is protocol-defined and not user-configurable. Add parser, request, progress, and error regressions.

## Validation

The full release campaign identified this missing command in the stock lifecycle lane. Focused source tests are added for typed command parsing, digest-preserving request encoding, quiet mode, and streamed Engine errors. Focused stock Bazel client and CLI targets pass (invocation `e465bc49-0179-4064-b581-a8f44b2c6b3c`). Full release qualification remains required.
