# Import Docker Compose service identity before native startup

## Motivation

Docker Compose supplied only Docker labels to the compatibility API. The native startup hosts barrier requires a complete native service identity, so the service could begin before dependency names were available. Import the complete project/service/oneoff identity at the HTTP create boundary, reject native conflicts, and preserve original Docker boolean spelling. Partial identities and one-off services do not gain aliases. Regression coverage checks the actual router boundary, identity conflicts, and native mirror validation.

## Validation

Focused regression tests and the complete source/package/runtime release gates are required. Stable publication remains conditional on zero semantic differences and verified host restoration.

## Compatibility and risks

No parity waiver or lower dependency rebuild is introduced. The release must qualify the exact signed package with both native providers and the Docker oracle.
