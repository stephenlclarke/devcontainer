# Consume the refreshed TLS dependency graph

The enhanced Devcontainer profile must resolve the refreshed Container and Containerization commits together with the reviewed `swift-nio-ssl` fork used by that graph. The stock profile remains on Apple `swift-nio-ssl` 2.37.4 and its existing exact lock. This profile split must be explicit so SwiftPM does not select an incompatible transitive TLS revision.

The package manifest change updates the build recipe identity for all eight SDK layers across both profiles, and the enhanced graph also changes its source and resolved-lock inputs. Existing layer archives therefore cannot be admitted as outputs for this graph. New producers must consume the updated graph in order and record fresh source, recipe and archive identities. Historical compatibility checks retain their original inputs and must reject the new graph unless a separately reviewed transition explicitly admits it.

Implementation and validation are recorded in the matching [PR handoff](PR-tls-layer-consumption.md).

Hosted SwiftPM preparation must authenticate its reviewed patch recipient at the same Containerization revision as the enhanced resolved graph. Its previous exact pin rejects the new graph before compilation; refreshing that pin must retain rejection of the preceding graph and preserve the reviewed patch bytes and digest.
