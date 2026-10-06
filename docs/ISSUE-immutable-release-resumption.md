# Immutable release resumption

## Problem

GitHub locks prerelease assets when release immutability is enabled. Publication run 37414187964 attempted to replace the already staged 1.1.0 assets and was rejected before installation. The existing archive, qualification and original publication-verifier identity are valid and immutable.

## Required result

Resume by authenticating the complete existing asset inventory, checksums and sizes against the qualified local assembly. Preserve the original publication-verifier asset only when its source and verifier code checksum match the selected candidate. Record repaired publication tools in separate attested workflow evidence. Complete actual Homebrew installation and strict restoration, then change only the prerelease/latest metadata to promote the existing release.

## Validation

Reject altered package bytes, altered downloaded identity, mismatched source or verifier code, incomplete or duplicate inventory, aliases and already final releases. Validate the authentic staged GitHub assets before resumption. Do not modify the immutable release assets, signed tag, package, benchmarks or qualification.
