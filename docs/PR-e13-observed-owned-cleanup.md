# PR handoff: prove auto-removed E13 guest absence

The foreground probe records an exact observed-owner receipt before applying the unchanged `Config.Image` and command checks. A failed semantic check retains the bounded raw inspect privately. Cleanup uses that receipt only after the owned Compose child has stopped and only to prove the same ID and name absent from direct and owner-filtered inventory. It never deletes a live guest whose semantic ownership fails.

Focused fake tests cover wrong `Config.Image` with auto-removal, a live mismatched guest that must not be deleted, and unowned observation rejection. The failed E13 campaign stays failed; this patch does not authorize runtime qualification or release. The paired [image provenance patch](ISSUE-config-id-image-provenance.md) supplies the separate positive proof for bare configuration IDs.

Issue: [E13 observed-owner cleanup](ISSUE-e13-observed-owned-cleanup.md).
