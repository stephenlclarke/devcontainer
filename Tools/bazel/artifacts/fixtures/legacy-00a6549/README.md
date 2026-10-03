# Archived 00a6549 lock fixtures

These nine lock files are byte-for-byte snapshots from devcontainer-bazel-workflow commit `00a65492cad492dda342bd62459595b3782968f3`. The eight profile/group locks preserve the producer receipts used by the archived stock and enhanced consumers; `argument-parser.lock.json` pins their shared lower archive input. `test_foundation.py` verifies each SHA-256 before using a snapshot, so tests continue to model the original release if live locks advance.

| Fixture | SHA-256 |
| --- | --- |
| `argument-parser.lock.json` | `06c5ef150dc92876484ff849980e69ad967d4fb0b66c4024f9b7e4b7832b68ce` |
| `container-sdk-enhanced.lock.json` | `c2784522152856079bd0d8099a8af151ef1e562f4d4902150346efa95079ff47` |
| `container-sdk-stock.lock.json` | `4d0470004fd40bb8d440109e792e993563840c0889a3a622dc045b88f99ffaeb` |
| `containerization-enhanced.lock.json` | `75010ab0b5e69f67e3d5aa4aa9e152bc7b0a3f26477af4fc501d8afc024da57f` |
| `containerization-stock.lock.json` | `0918dab08b02c8b9aa5c13db8c2c165c245fee9ce579a7de4ac87b1e68c1600f` |
| `engine-api-enhanced.lock.json` | `007dcded692ff84c64b88cfef2211e62e3bf321985689908440b332b29edfcee` |
| `engine-api-stock.lock.json` | `a5cb5830748d5d2f858597b6c1ffce60eefbb45482eef69ad35369b705ab8d3c` |
| `foundation-enhanced.lock.json` | `5383f27b335a66de065102515bf7ff3644b8ad784f409c15a8d3b5be6c908956` |
| `foundation-stock.lock.json` | `986d309ad2ced14a5656a6d9291e174a7e5afc12ea8d7db649fed809eebf6495` |

The source checkout used as the fixture seed is not release evidence. Package inputs are normalized to their authenticated 00a6549 baseline by the focused test; archived compact lock bytes always come from this directory.
