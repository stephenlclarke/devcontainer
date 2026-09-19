# Testing

The native Bazel harness prepares its official Dev Container CLI reference with `make bazel-prepare-devcontainers-cli`. It retains pinned CLI 0.88.0 and prebuilt Node 24.21.0 privately, without a global installation, npm hooks or a product rebuild; `OFFLINE=1` verifies retained inputs without network access or repair. Official npm/Node distributions are an explicit exception to GitHub release-asset sourcing. These are reference-only dependencies, not requirements for the Docker-free candidate. Preparation and version probes pass; D01 runtime parity remains incomplete.

The native D01 Docker adapter executes the checked-in image configuration and probe through that CLI in an isolated VM. It retains phase/command timings and private diagnostics, enforces whole-phase deadlines, and supports cleanup-only recovery after known command completion. The Docker reference passes all four observations and cleanup at source `c6daa35`, invocation `83e7687b-5db8-4788-8dc2-5cf3b33ca656`. Stock candidate D01 also passes at harness `e346eed`, invocation `46a33e35-686a-45f4-9168-80ba6ecafa31`, using the private bundled CLI/frontend. Enhanced published guest prerequisites remain blocked. These are functional-run observations, not quiet paired benchmarks or full three-lane qualification. No missing adapter is skipped or counted as passing.

D02 Dockerfile configuration is the active native harness contract. Its Docker reference passes build arguments, target, post-create output, workspace and cleanup at `05cae9e`, invocation `0686509c-bf9c-4ba3-a405-02fb0ebca041`. The unreleased candidate build frontend sends the captured local-context command through the shared gateway with bounded preparation/streaming, external-Dockerfile exclusion and joined signal cancellation. Its adapter requires the released private builder and exact image ancestry from native `RootFS.Layers`. Candidate live execution and interrupted-build recovery remain outstanding. Existing `.dockerignore` files and unimplemented build options fail explicitly; this is not blanket Docker-build compatibility.

The unreleased native Bazel candidate tests failed image-build progress, in-band error records and failed-operation bookkeeping in both profiles, including cancellation and abandonment. These component checks do not establish live E04 build parity or replace the historical release evidence below.

The opt-in E04 adapter uses published, digest-verified builder images and isolated runtime state. Its negative build requires a unique guest execution marker and exit code 1, so setup failures cannot pass as expected command failures. Output images and the private builder have separate ownership journals; uncertain completion preserves quarantine. Component tests pass, but full live qualification and interrupted-build recovery remain release gates.

Completed Docker builds can be reconciled with the report-first Bazel runtime recovery command. Apply requires the exact case ID and verifies the original tools, VM processes, terminal responses and image ownership before scoped cleanup. Recovery never rebuilds an image or changes a failed test into a pass. Unknown build completion and interrupted VM shutdown remain quarantined.

The Docker E04 lane passes positive build, failed-command execution and cleanup. After correcting image-list filtering, the stock Apple candidate also passes all three observations and cleanup (`0acf86ea-3bc4-47e5-8468-fba047e51352`). Stock F01 subsequently passes all five lifecycle/fault assertions and cleanup (`d996ac7c-d3d4-494e-a26a-793dfa800c89`). Enhanced live qualification remains blocked, and these separate candidate runs do not replace full published-release parity. Explicit named-reference build cleanup stays inside the exclusive private Apple store, with tag/config/manifest checks and garbage collection disabled. This is not general image-ID deletion parity; digest-addressed public mutations remain refused. Resource-only recovery can reconcile the captured builder without adopting a legacy Engine process or rewriting failed evidence. Functional-run timings are retained separately from quiet comparative benchmarks.

The hosted-safe suite contains unit, contract, state-recovery, malformed-input,
archive-safety, and HTTP wire tests. Aggregate first-party Swift line coverage
must remain at or above 90 percent. Address Sanitizer and Thread Sanitizer run
in separate clean build directories and fail on crashes or sanitizer findings.
CI discovers the complete hosted-safe Swift suite at execution time and
enforces at least 90 percent first-party line coverage without relying on a
manually maintained test count. CI also enforces at least 90 percent
coverage on changed executable lines and retains LCOV plus Sonar generic XML
evidence.

Trusted physical Apple-silicon runners execute the real parity lanes. The
Docker oracle captures normalized results first; stock Apple and the optional
Compose provider must then match those results. A pinned VS Code and Dev
Containers extension perform open, attach, terminal, port, rebuild, reopen, and
cleanup flows.

Real Docker, stock Apple `container` 1.1.0, and the matched
`container-compose` 0.10.1 provider pass all 18 CLI fixtures and the real VS
Code fixture without normalized semantic differences and with complete timing
evidence.

That result is the immutable stable-release baseline. The 15 September 2026
runtime workflow for current source did not produce complete lane result files,
so it supplies no replacement runtime or timing certification. Hosted quality
checks for that source passed independently with 95.5 percent SonarQube
coverage and 0.1 percent duplicated lines.

In the exact 1.0.0 tag run, the largest CLI ratios are 2.876x for stock Apple
and 4.509x for `container-compose`; the corresponding VS Code ratios are
1.232x and 1.311x. Release evidence includes the normalized comparison, raw
recordings, diagnostics, fingerprints, JUnit, and cleanup reports for all
three lanes.

Every CLI fixture also records monotonic wall-clock time in lane JSON and
JUnit. The comparison artifact retains raw durations and reports stock/Docker
and provider/Docker ratios even when functional parity fails. Comparable or
better performance (`<=1.00x` Docker) is the objective. A completed result
above `2.50x` Docker requires further investigation but does not, by itself,
change functional parity. A timeout, other non-completion, or missing or
invalid timing evidence fails the parity gate. The full target is in
[PARITY-ROADMAP.md](https://github.com/stephenlclarke/devcontainer/blob/main/PARITY-ROADMAP.md).

See <doc:Performance> for the three-run matrix, variability, phase analysis,
and optimization priorities. See <doc:Conformance> for properties that the
release fixtures do not certify.
