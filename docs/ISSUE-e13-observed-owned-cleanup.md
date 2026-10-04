# Issue: preserve exact guest identity after E13 image mismatch

The enhanced E13 guest was observed under its exact case owner, but `Config.Image` differed from the requested configuration ID. The strict readiness check correctly failed. Automatic removal then left no running guest, while cleanup had no durable observed identity with which to prove absence and release the owned project claim.

Record the observed 64-hex ID, exact name, case owner, top-level image and intent hash before semantic readiness checks. Retain a bounded private failed inspect record. After the owned Compose child stops, use the observation only to prove that the exact ID and name are absent from both direct and owner-filtered inventory. Any live guest still requires full existing semantic ownership before deletion. This cleanup receipt cannot become `container-created.json`, a readiness pass, or qualification evidence.

Implementation handoff: [PR observed-owner cleanup](PR-e13-observed-owned-cleanup.md). The [image provenance handoff](ISSUE-config-id-image-provenance.md) separately corrects the requested image spelling.
