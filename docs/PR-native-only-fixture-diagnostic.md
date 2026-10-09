# Add bounded native-only package diagnostics

## Motivation and implementation

The controller now accepts repeated `--diagnostic-fixture` options for C03 resources, D05 features and E07 attachment. It uses the existing native API preflights, shared runtime lease and host guard, then runs only the selected CLI fixtures in Apple stock and container-compose. VS Code is skipped and the Docker oracle lane is never started. Signed finalized-package admission remains unchanged. The mutually exclusive `--candidate-invocation` path admits only a retained schema-2 stock candidate whose source commit matches the clean product checkout; it rechecks the exact archive, executables, private Node/CLI inventory and terminal launcher inventory before and after each native lane. Candidate evidence uses `unsigned-native-candidate`, sets `signatureVerified: false`, and cannot enter complete qualification. Both forms use the `native-only-fixture-diagnostic` scope with `releaseQualified: false`, `dockerOracle: not-run` and `comparison: not-run`.

An optional Docker result file requires a matching independently supplied SHA-256. The controller copies its exact bytes under a separate `reference-input` directory and records its hash without placing it in lane results or comparing it as current-source evidence. Product source and tooling source identities are recorded independently for the two-checkout run; product inputs remain pinned to the selected package source while the current tooling checkout supplies the controller and runner. Package admissions, provider identities, fixture outputs and host restoration are rechecked before the diagnostic receipt is written. The existing full qualification branch and component mode remain unchanged.

## Validation

The focused controller tests cover finite selector validation, CLI-only execution, diagnostic preflight with no Docker callback, exact signed-package proof rejection, candidate mode restrictions, wrong candidate source/profile, incomplete candidate inventory rejection, separately authenticated reference-file hashing, non-qualifying receipt fields, native restoration requirements, and unchanged full CLI/V01 behavior. Both focused parity suites pass: 84 controller tests and 54 runner tests. Python syntax compilation and Markdown lint pass. No live runtime, Docker oracle lane, build, package signing, notarization, comparison or release qualification is performed by these checks.

## Compatibility and remaining risks

The mode is diagnostic evidence only. It does not establish three-lane parity, compare against an external reference, satisfy the 84-observation gate or grant release authority. The full qualification path retains its existing Docker-first ordering and complete comparison requirements.

Linked issue: [bounded native-only package diagnostics](ISSUE-native-only-fixture-diagnostic.md).
