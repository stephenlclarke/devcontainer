# PR: isolate Docker parity's BuildKit cache per campaign

## Summary

The Docker reference now uses a nonce-named `docker-container` Buildx builder with an immutable linux/arm64 BuildKit v0.30.0 image instead of the persistent default builder. Before fixtures, the harness records the fresh cache identity and verifies the exact container, pinned image, and local volume mounted at `/var/lib/buildkit`. Cleanup removes only that builder and its exact volume, verifies their absence, and retains a result receipt. Existing within-campaign BuildKit caching stays enabled.

## Validation

Focused Python parity and manifest tests pass, including create/bootstrap failure, foreign-name collision, changed container or volume identity, and successful cleanup. No Docker runtime or fresh parity campaign was launched for this source change. The original `bed73cec` timing failure remains retained; release authority requires a new full campaign and all existing gates.

## Compatibility and risk

The Docker reference CLI, Engine endpoint, Feature fixture, native providers, package bytes, and timing threshold are unchanged. The fresh builder can pull its pinned image before timed fixtures. If its ownership or removal is uncertain, parity fails closed and preserves the host guard. D06 image-store asymmetry is a separate known observation.

## Related work

See [the cache-isolation issue](ISSUE-parity-cold-docker-builder.md). This source correction does not claim that the previously failed campaign passed.
