# Issue: conservative recovery for native release qualification

## Problem description

PR 83's unattended release path must recover interrupted work without converting an unknown outcome into a pass or deleting a replacement resource. A native create can complete before identity verification or metadata publication fails. After restart, an identifier or owner label alone cannot prove that the currently observed object is the one created by the interrupted operation. Related launcher, source-fingerprint, build-evidence and process-ownership review comments also require exact inputs before recovery or qualification can proceed.

## Required behavior

Recovery may reconcile a durable create intent only when the exact runtime identifier, strictly verified persisted native configuration and native creation timestamp all match the intent. The successful reconciliation must atomically publish the compatibility metadata and clear that exact intent. Missing or unavailable inventory, absence, replacement, or any mismatch retains quarantine and reports the operation identifier and captured timestamp with explicit no-delete/no-clear guidance. Recovery must never retry the create or delete by ID, name, or owner label alone.

Other native operations must not mutate an object while its matching create is pending; shutdown requires the complete captured PID-role set and refreshed process identities. Evidence admission must use the exact maintained launcher and runtime target closure. Invalid JSON numeric constants must be rejected throughout build records. A retained parity report is read-only, accepts only bounded campaign/fixture/format arguments and must work without the SSD, Bazel, Xcode or build scratch while continuing to authenticate the private internal evidence database.

## Current status

The qualification launcher changes for PR 83 discussions 21 and 25 have focused passing regressions. The final Python evidence and recovery batch reports 100 focused tests passing; its earlier 97-test result preceded the BuildKit configuration-ID regression. An initial SwiftPM test attempt stopped before compilation because stale inputs selected conflicting Containerization revisions `b404e03` and `6db1619`; the pins are now aligned, and package resolution plus live source-graph validation pass. Focused native Bazel compilation/tests remain pending refreshed enhanced dependency archives. No Swift recovery test or live E13 component or complete 84-observation qualification is claimed here.

The Q runtime `f86fea2236fab118c0e0c6f8be5eb7672df894e2` is published and verified after download. Compose SDK `1a96` is published and its downloaded asset is verified, while complete Compose product qualification remains open. Final Dev source freeze, exact-head quality admission, native package finalization and full 84-observation qualification remain required. Existing lower archives may be reused only under their exact authenticated compact-lock and finite recipe compatibility checks. Performance work remains deferred until functional release gates are complete.

## Related review threads

These are the original PR 83 review identities; this handoff does not create replacement issue or review IDs:

- [18 — Merge image labels into the container configuration](https://github.com/stephenlclarke/devcontainer/pull/83#discussion_r4041089799)
- [19 — Reconcile the container when identity verification fails](https://github.com/stephenlclarke/devcontainer/pull/83#discussion_r4041089809)
- [21 — Reject Bazel's `-D` shorthand for protected defines](https://github.com/stephenlclarke/devcontainer/pull/83#discussion_r4041284542)
- [22 — Guard internal reconciliation from pending creations](https://github.com/stephenlclarke/devcontainer/pull/83#discussion_r4041457516)
- [23 — Require the complete PID-role set before shutdown](https://github.com/stephenlclarke/devcontainer/pull/83#discussion_r4042086988)
- [24 — Include the launcher in the campaign fingerprint](https://github.com/stephenlclarke/devcontainer/pull/83#discussion_r4042161166)
- [25 — Run read-only reports before SSD enrollment](https://github.com/stephenlclarke/devcontainer/pull/83#discussion_r4042237278)
- [26 — Fingerprint the Bazel runtime target definition](https://github.com/stephenlclarke/devcontainer/pull/83#discussion_r4042618475)
- [27 — Reject non-JSON numeric constants in build streams](https://github.com/stephenlclarke/devcontainer/pull/83#discussion_r4043690293)
- [28 — Preserve unjournaled replacement images during recovery](https://github.com/stephenlclarke/devcontainer/pull/83#discussion_r4043815766)

Tracks [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83). This issue handoff records the recovery and admission requirements; it does not declare the product or runtime qualified.
