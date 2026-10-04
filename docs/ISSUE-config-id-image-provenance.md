# Issue: a resolved tag replaced a requested OCI configuration ID

The retained enhanced E13 observation requested a bare `sha256:<config ID>` and reached native guest creation, but the gateway's `Config.Image` and stored spec showed the resolved tag. The native manifest descriptor and OCI configuration digest are distinct identities. The E13 semantic image assertion failed; cleanup and runtime qualification remain separate.

Compose must carry the requested spelling in its reserved native provenance label. Devcontainer must accept a bare ID only after fetching the OCI configuration digest through the observed native descriptor and selected platform, then re-reading the same container incarnation before metadata adoption. A tag or historical metadata image ID cannot supply that proof. Repo-qualified manifest digest behavior and tag spelling remain unchanged.

Implementation handoff: [PR config-ID image provenance](PR-config-id-image-provenance.md).
