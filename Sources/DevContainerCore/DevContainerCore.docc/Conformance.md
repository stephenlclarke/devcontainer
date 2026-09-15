# Standards Conformance

Version 1.0.2 is the current release candidate. Its fixture scope becomes a
stable conformance claim only after the release-bound parity gate passes. It
is not a complete implementation of every property in the Development
Containers Specification.

The audit uses an exact upstream
[`devcontainers/spec` commit](https://github.com/devcontainers/spec/tree/c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421)
and the exact official
[`@devcontainers/cli` 0.89.0 commit](https://github.com/devcontainers/cli/tree/5dc7533314b5ba7ec3875c30143dfe1aec644870)
used by the release.

## Confirmed 1.0.2 non-conformances

- Arbitrary `runArgs` are not a blanket pass-through. Modelled request objects
  reject unknown fields, but complete schema-derived endpoint coverage remains
  open.
- `hostRequirements.gpu` becomes a decoded Docker device request that stock
  Apple rejects before creation because no certified GPU primitive exists.
- Stock privileged mode is rejected before creation rather than approximated
  with `--cap-add ALL`.
- Stock Apple cannot transport security options other than the already-native
  `seccomp=unconfined` state.
- Stock Apple cannot set an explicit container hostname. The enhanced
  Container distribution advertises a native hostname flag that the adapter
  forwards, but hostname parity is not yet independently release-certified.
- Advanced Docker mount options are decoded but rejected when their semantics
  cannot be enforced.
- Image-declared anonymous volumes use Apple's writable root filesystem rather
  than a separate Docker anonymous-volume lifecycle.
- Stock Apple cannot connect or disconnect networks after container creation.
- Resource, namespace, device, DNS, host mapping, restart, and similar Docker
  run arguments are decoded and fail explicitly when unsupported; they remain
  outside the claim.
- Docker daemon bind mounts are rejected. The installed adapter accepts only
  the project-owned local `engine.sock` and verifies its exact engine identity
  before the first workload request, so a renamed foreign Docker-compatible
  socket cannot become a runtime backend.

Properties owned by the official CLI or VS Code are separately classified as
delegated, partial, or unverified. A delegated parser/UI feature can still
depend on unsupported runtime behavior.

Version 1.0.2 is limited to Linux `arm64` containers on Apple-silicon macOS
Tahoe hosts. That is a product support boundary rather than a standards
non-conformance.

Docker-named types, fields, and the `devcontainer-docker` executable are
protocol compatibility vocabulary for the unmodified official clients. The
release archive and Homebrew installation contain no Docker Engine, Docker
Desktop, Docker CLI, Docker Compose, Colima, Podman, nerdctl, daemon socket, or
runtime fallback. Real Docker runs only in the isolated reference-oracle lane
and is never a candidate product dependency.

The complete property-by-property ledger, impact, workarounds, and remediation
priorities is maintained in
[CONFORMANCE.md](https://github.com/stephenlclarke/devcontainer/blob/main/CONFORMANCE.md).

The prioritised design for closing those gaps is maintained in
[PARITY-ROADMAP.md](https://github.com/stephenlclarke/devcontainer/blob/main/PARITY-ROADMAP.md).

The field-by-field implementation and certification design for every current
unsupported capability is maintained in
[UNSUPPORTED-CAPABILITIES.md](https://github.com/stephenlclarke/devcontainer/blob/main/UNSUPPORTED-CAPABILITIES.md).
