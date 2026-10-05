# VS Code's neutral context marker rejects the selected local socket

## Problem

The pinned Dev Containers extension initializes its Docker subprocess environment with `DOCKER_CONTEXT=default`, including when it preserves an explicit `DOCKER_HOST`. The adapter rejects every environment context marker and consequently fails its first server-version query. The stock VS Code fixture cannot attach.

## Expected behavior

Accept the neutral `default` marker when an explicit local Unix endpoint is supplied. Keep named context selection, absent endpoints, unsafe socket paths, and remote endpoints rejected. Do not discover Docker contexts or change the selected runtime.

## Validation

A parser regression reproduces the extension's server-version invocation and confirms the exact socket. Negative cases cover a default marker without an endpoint, a named context with a socket, and remote or empty endpoints. Full real VS Code qualification remains required.
