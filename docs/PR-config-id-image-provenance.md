# PR handoff: prove configuration-ID image aliases before projection

Typed and enhanced CLI inventories validate a bare Compose image-reference label against the selected native descriptor/platform's OCI manifest config digest. Typed observations re-read ID, creation and start generation, labels, reference, descriptor, and platform after the asynchronous lookup; the CLI precision path retains its native-incarnation check. A matching bare ID supplies the observed image ID. Conflicting same-incarnation metadata fails without mutation, and an older incarnation cannot be adopted. Creation finalization passes its original deadline and cancellation context through the same verification. Planned network-hosts configuration uses a separate proof path because it has no live native incarnation.

Tests cover valid bare IDs, wrong IDs, manifest-vs-config mismatch, mutable-tag preservation, replacement during lookup in list/inspect/creation, expired and cancelled creation, and conflicting metadata. The paired Compose patch carries the requested bare ID in a reserved label without changing the native image operand. The retained E13 failure is not a qualification pass; new exact-source runtime proof is required.

Issue: [configuration-ID image provenance](ISSUE-config-id-image-provenance.md).
