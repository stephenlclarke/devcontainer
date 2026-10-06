# Resume an immutable staged release

## Result

A stable retry authenticates all immutable staged asset names, sizes and SHA-256 values before preserving the original publication-verifier identity in its assembled scratch output. A source or verifier-code mismatch fails closed. No release upload or deletion occurs for an authenticated immutable retry.

## Evidence and compatibility

The workflow separately attests and retains the current publication operation, including its tool commit, verifier and installation-helper checksums and the complete immutable asset map. The original public verifier provenance remains unchanged and its original commit is recorded in the new operation receipt. Actual installation, strict baseline restoration, tap promotion and final prerelease/latest metadata changes remain required. Initial releases and mutable staged releases retain their existing publication path.

## Validation

Focused boundary tests cover exact resumption and negative package, metadata, source, verifier, inventory, alias and final-release cases. The actual staged 1.1.0 assets must pass the same validator before the workflow resumes. Production source, the runtime harness, archive, signing, notarization and qualification remain unchanged; no heavyweight requalification is required for this publication-only recovery.
