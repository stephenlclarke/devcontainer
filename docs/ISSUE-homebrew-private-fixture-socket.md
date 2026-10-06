# Homebrew private fixture socket

## Problem

The genuine installed 1.1.0 package passed audit, fetch, installation and version probes but its Homebrew configuration-read test failed. The Ruby fixture created a Unix socket with inherited group or other permissions. The released client correctly requires a private user-owned socket. The failed transaction restored the original baseline.

## Compiled identity validation

After correcting the socket, the actual formula test passed. The final installation validator then compared the compiled candidate build lane with the stable distribution lane. Native package smoke tests explicitly require the compiled lane to remain candidate; promotion changes the separately authenticated package context, without rebuilding the qualified executable. The validator must require that exact candidate lane alongside the selected full commit and product version.

## Required result

Create the Homebrew test socket with mode 0600. Keep the client security contract intact, preserve all configuration assertions, and render and validate against the same pinned maintained installation template. Record the updated template checksum separately from immutable package assets.
