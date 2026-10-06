# Fix Homebrew renderer contract

## Result

Stable formula validation now includes the renderer's blank conflict declaration. A focused regression executes the actual renderer and validates its complete output; existing negative formula tests retain exact-byte enforcement.

## Publication and compatibility

The separately pinned publication-tool checkout also supplies the corrected installation helper and its unchanged testing dependencies. Release provenance records its checksum alongside the verifier identity. The staged 1.1.0 package and signed tag remain immutable, and source/package/runtime admission and strict Homebrew baseline restoration are unchanged.

## Validation

Run the focused installation tests, workflow and Markdown checks, then admit the actually rendered 1.1.0 candidate without executing an installation. Only after that proof succeeds, resume publication and require a successful real installation/restoration receipt before promotion to a stable release.
