# Standards Conformance

Version 1.0.1 is conformant for its release-certified fixture scope. It is not
a complete implementation of every property in the Development Containers
Specification.

The audit uses an exact upstream
[`devcontainers/spec` commit](https://github.com/devcontainers/spec/tree/c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421)
and the exact official
[`@devcontainers/cli` 0.88.0 commit](https://github.com/devcontainers/cli/tree/f683c29f64a20109b4453e5149807e390ff65133)
used by the release.

## Confirmed 1.0.1 non-conformances

- Arbitrary `runArgs` are not a blanket pass-through. Unknown Docker create
  members can be ignored instead of rejected.
- `hostRequirements.gpu` becomes a Docker device request that 1.0.1 does not
  decode or transport.
- Stock privileged mode maps to `--cap-add ALL`, not full Docker privileged
  semantics.
- Stock Apple cannot transport security options other than the already-native
  `seccomp=unconfined` state.
- Stock Apple cannot set an explicit container hostname.
- Advanced Docker mount options are not represented.
- Image-declared anonymous volumes use Apple's writable root filesystem rather
  than a separate Docker anonymous-volume lifecycle.
- Stock Apple cannot connect or disconnect networks after container creation.
- Resource, namespace, device, DNS, host mapping, restart, and similar
  arbitrary Docker run arguments are outside the 1.0.1 DTO and claim.

Properties owned by the official CLI or VS Code are separately classified as
delegated, partial, or unverified. A delegated parser/UI feature can still
depend on unsupported runtime behavior.

Version 1.0.1 is limited to Linux `arm64` containers on Apple-silicon macOS
Tahoe hosts. That is a product support boundary rather than a standards
non-conformance.

The complete property-by-property ledger, impact, workarounds, and remediation
priorities is maintained in
[CONFORMANCE.md](https://github.com/stephenlclarke/devcontainer/blob/main/CONFORMANCE.md).
