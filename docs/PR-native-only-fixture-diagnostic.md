# Add bounded native-only package diagnostics

## Motivation and implementation

The controller now accepts repeated `--diagnostic-fixture` options for C03 resources, D05 features and E07 attachment. It uses the existing native API preflights, shared runtime lease and host guard, then runs only the selected CLI fixtures in Apple stock and container-compose. VS Code is skipped and the Docker oracle lane is never started. Signed finalized-package admission remains unchanged. The mutually exclusive `--candidate-invocation` path admits only a retained schema-2 stock candidate whose source commit matches the clean product checkout; it rechecks the exact archive, executables, private Node/CLI inventory and terminal launcher inventory before and after each native lane. Candidate evidence uses `unsigned-native-candidate`, sets `signatureVerified: false`, and cannot enter complete qualification. Both forms use the `native-only-fixture-diagnostic` scope with `releaseQualified: false`, `dockerOracle: not-run` and `comparison: not-run`.

An optional Docker result file requires a matching independently supplied SHA-256. The controller copies its exact bytes under a separate `reference-input` directory and records its hash without placing it in lane results or comparing it as current-source evidence. Product source and tooling source identities are recorded independently for the two-checkout run; product inputs remain pinned to the selected package source while the current tooling checkout supplies the controller and runner. Package admissions, provider identities, fixture outputs and host restoration are rechecked before the diagnostic receipt is written. The existing full qualification branch and component mode remain unchanged.

For a D05-only run, `--d05-cache-state warm` first provisions the authenticated native provider HOME's kernel and initialization inputs, then adds one untimed `devcontainer build` against the same copied fixture before the existing functional timing interval. This prerequisite work does not run or claim the E07 fixture operation, and it does not use the E04 builder path. The diagnostic separately verifies that both `common-utils_0` and `git_1` stages were reported `CACHED` and that no apt installation occurred. Cache proof failures keep the functional fixture result intact and mark cache comparison ineligible. The option is rejected for full qualification, component checks and any selection other than the single D05 diagnostic; omitting it keeps the existing cold path.

A C03-only native selection also provisions the authenticated provider HOME's default kernel and initialization images before Engine startup. It reuses `ReleasedGuest`'s E07 provision-only path, while the selected operation remains C03 and no E07 fixture operation or E04-specific builder preparation is run. Other single-fixture selections keep their existing preparation behavior; Docker's provider setup is unchanged.

## Validation

The 11 `ComponentBuilderSelectionTests` pass, including the runner regression that verifies C03-only native diagnostics provision before Engine startup in both native lanes and then invoke only C03. All 6 `NativeProvisionBeforeEngineTests` pass; the new provisioner regression proves it uses the authenticated E07 preparation inputs without an E07 operation or builder inputs. Python syntax compilation and Markdown lint pass. No live runtime, Docker oracle lane, build, package signing, notarization, comparison or release qualification is performed by these checks.

## Compatibility and remaining risks

The mode is diagnostic evidence only. It does not establish three-lane parity, compare against an external reference, satisfy the 84-observation gate or grant release authority. The full qualification path retains its existing Docker-first ordering and complete comparison requirements.

Linked issue: [bounded native-only package diagnostics](ISSUE-native-only-fixture-diagnostic.md).
