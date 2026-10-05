# Compose runtime qualification is blocked by resource lifecycle errors

The retained bf02d495 campaign fails C04 lifecycle against the signed stock gateway at 79b3c929: recreation changes generated configuration-file provenance and rejects the same owned network, while repeated cleanup attempts to delete a volume already removed by the first down. Its failure evidence remains unchanged.

The separately released lower correction is Compose `5a60352febdbd2db04216c65b9b60443b1bf5d39`. Devcontainer must consume both verified GitHub assets from that source and update its parity provider commit at the same time, keeping all other dependency release inputs unchanged. Its independent event-inventory correction is described in [the event issue](ISSUE-event-inventory-conflict.md).

See [release-input implementation and validation](PR-compose-resource-release-input.md). A fresh exact-main package and complete runtime campaign are required before the stable Devcontainer release.
