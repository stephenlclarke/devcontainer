# Publication harness identity

## Problem

Prebuilt Binaries run 37412929401 rejected the signed 1.1.0 candidate before publication because its verifier hashed seven files, while the runtime runner and authenticated local qualification hashed the maintained 54-file closure. The qualified source is immutable tag `1.1.0` at `297c9dde27f6519d6ba5b02314622e4a21c88702`.

## Required result

Release verification must hash the ordered literal `PARITY_HARNESS` declared by the selected tagged source. It must retain all archive, source, artifact, qualification, timing and restoration checks. The tag, package and recorded measurements must remain unchanged.

## Validation

Compare the real runner and release verifier without mocking the hash. Confirm that changing a previously omitted helper changes the identity and that missing, symlinked, nonliteral, duplicate and unsafe closure inputs are rejected. Admit the existing authenticated GitHub parity artifact against the retained finalized package with the repaired publication verifier.
