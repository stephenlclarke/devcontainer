# Compose network release input

## Problem

The Devcontainer full runtime campaign at `5bda8798320dd933be21511b602df71cf6f80a4c` failed C04 in the Compose lane because the stock Engine gateway rejected repeated network creation with HTTP 409. Repeated cleanup also rejected an already absent network. The original failed evidence and completed owned cleanup remain preserved.

## Expected behavior

Consume the separately published gateway that reuses only an exact compatible owned network and accepts deletion only after confirming absence. All other network errors remain failures. Keep the existing lower compiled dependencies and runtime inputs.

## Linked work

See [implementation and validation](PR-compose-network-release-input.md). The lower fix is `stephenlclarke/container-compose` commit `79b3c92930a64fa151b856f2254db8d16825661d`.
