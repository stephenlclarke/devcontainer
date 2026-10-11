# Isolate the Docker Feature-build cache for parity timing

## Problem

The signed `bed73cec` campaign passed the 84 functional parity cells but failed its existing 10× D05 timing gate: Docker took 4.474 seconds and container-compose took 59.946 seconds. Docker's Buildx output marked both Feature installer layers `CACHED`, while the native lanes executed the installers. The Docker lane selected the persistent default builder, so its result could inherit layer cache from earlier campaigns. The failed campaign remains retained as mixed-cache evidence, not a product-speed result.

## Expected behavior

Each Docker campaign must create a fresh, exact-owned BuildKit builder and cache volume before fixture timing, retain normal caching among fixtures within that campaign, and prove both resources absent during cleanup. The BuildKit image must be pinned to the verified linux/arm64 v0.30.0 manifest and the observed daemon version must match. Failures during create, bootstrap, admission, or removal must leave an explicit failed cleanup receipt; no unrelated builder or cache may be pruned.

## Scope

Change Docker parity builder ownership and its manifest admission only. Preserve the 27 CLI fixtures, three providers, seven-trial timing policy, 10× gate, Docker daemon and pinned CLI, native builds, and all original raw evidence. D06's image-store asymmetry is tracked separately and does not justify a daemon/profile migration here.

## Validation

Deterministic parity tests cover the pinned image, fresh name, bootstrap version, exact container/cache-volume linkage, partial create cleanup, collision refusal, changed ownership refusal, and terminal absence receipt. A fresh full release campaign remains required to establish comparable timing.
