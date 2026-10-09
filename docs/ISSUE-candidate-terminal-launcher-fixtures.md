# Admit complete terminal-launcher candidates in guest fixtures

<!-- markdownlint-disable MD013 -->

The current schema 2 unsigned candidate has the legacy five executable entries plus `terminal-launcher-arm64` and `terminal-launcher-amd64`. `prepare_candidate.admit_candidate` authenticates both launcher files and their receipt hashes, but `released_engine.fixture_guest_inputs` required exactly the legacy five names. D05 therefore stopped at fixture admission before starting a guest or running its assertions.

Keep the five-name legacy layout valid. Admit the seven-name layout only when both launcher names, both 64-character receipt digests and the Go SDK licence digest are present. Reject partial inventories, unexpected names and missing or malformed proof; leave archive admission and profile selection unchanged.
