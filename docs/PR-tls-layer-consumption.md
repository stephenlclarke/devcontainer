# Refresh Devcontainer's enhanced TLS dependency input

The enhanced profile now pins the aligned Container and Containerization commits and selects their SwiftNIO SSL fork at `aee34db2144717ddce7bd145e45cf4fb9dab73fb`. The stock profile keeps Apple SwiftNIO SSL `2.37.4` at `03827c1a9fdb2b6b00a4e93ede8861520263af8c`; a clean stock-profile SwiftPM resolve reproduced `Package.stock.resolved` byte-for-byte. The direct package declaration lets SwiftPM resolve the fork consistently with Container's transitive requirement and does not add an unused product to a target.

The enhanced lock was refreshed by SwiftPM with package path overrides unset. Its selected Container, Containerization, Engine API and TLS pins match the reviewed source graph, and unrelated resolved pins remain unchanged. The Package.swift change changes the build-recipe identity for all eight SDK layers across both profiles, so each profile's consumers must be freshly produced and admitted in dependency order even though the stock source pins are unchanged. The historical SDK-lock transition tests use byte-checked pre-TLS package inputs; a new regression verifies that the refreshed TLS graph is not accepted by the archived enhanced SDK lock. No production admission rules or archived locks were changed.

No dependency layer was built or published as part of this change. Fresh consumer archives for both profiles must be produced from the refreshed graph before they can be used as canonical inputs.

The hosted SwiftPM patch preparation policy now requires the same Containerization `c0607ac9` source as the enhanced manifest. The reviewed ext4 patch and its digest remain unchanged because this source update changes only dependency inputs and documentation. The checked-in profile admission tests accept the new enhanced graph and continue to reject its preceding `6db16197` patch recipient. The original source-quality failure is retained.

Integrated `make format-check lint parity-manifest` passed: 695 Python cases with one existing skip, strict Swift lint and formatting, Markdown and shell checks, workflow lint and immutable parity fixture validation. The preceding preparation-pin failure remains retained.
