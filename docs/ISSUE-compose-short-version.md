# Native Compose short-version probe fails in the stock C01 lane

## Problem

The maintained `devcontainer-compose` facade handles the upstream CLI's `version --short` probe by forwarding the same Docker-facing arguments to the selected native provider. In the stock-backend C01 fixture, the upstream CLI fails during Compose-version discovery before it can create the project. The provider's admitted machine-readable version interface is `version --format json`, which reports its version and source.

## Expected behavior

When the facade selects `container-compose`, it should obtain the provider's version through the admitted JSON interface, validate the reported source and version, and return a truthful `container-compose <version>` value for the Docker-facing short-version probe. Docker-provider probes and ordinary native version commands should retain their existing behavior.

## Scope

Update only the maintained Compose facade and its tests. Do not change the separately installed provider, runtime backend defaults, or provider selection.
