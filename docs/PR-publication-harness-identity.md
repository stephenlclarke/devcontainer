# Fix publication harness identity

## Result

The release verifier reads the ordered literal harness closure from the selected source instead of maintaining a second file list. It fails closed on malformed declarations, unsafe paths, missing files and symlinks. Regression coverage compares its result with the actual runtime runner and exercises previously omitted helpers.

## Publication boundary

The workflow checks out the immutable release source separately from publication tools pinned to the workflow commit. Both checkouts are checked for exact identity and cleanliness. Only the corrected proof verifier comes from the publication-tool checkout; its repository argument remains the tagged release source. An attested release asset records the publication-tool commit and verifier checksum separately from the package source identity.

## Compatibility and validation

This repair changes no production Swift code, runtime harness member, signed archive, notarization submission, benchmark, qualification receipt or stable tag. Existing exact-source authorities and the unchanged timing policy remain required. Focused verifier tests, source quality and workflow validation cover the repair; acceptance of the authenticated retained artifact verifies the real publication input without rerunning runtime tests.
