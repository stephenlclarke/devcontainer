# Issue: preserve unchanged layers when updating Container SDK

Updating the enhanced Container revision changes the root Swift package manifest and resolved origin hash. The compiled-layer recipes bind that manifest, and the stock SDK source graph also records the enhanced lock. Consequently a change confined to the enhanced Container SDK can invalidate unrelated released libraries and the unchanged stock SDK.

Retain the existing released bytes and their original source, recipes and evidence. Admit only the exact archived `00a6549` manifest/lock with a sole full-length enhanced Container revision and matching origin-hash change. All other package, toolchain, producer, lower-layer, archive and source-graph identities must remain authenticated. Rebuild and publish the enhanced Container SDK against the new source; its old archive cannot satisfy the new pin.

This is part of [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83). It does not replace full product qualification, signing, notarization or runtime parity, and does not authorize publication from historical product evidence.
