# Testing

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
Compose provider must then match those results. Before either Apple candidate
starts, the harness independently stops any running Docker oracle and fails if
it cannot establish a quiet Docker-free host. A pinned VS Code and Dev
Containers extension perform open, attach, terminal, port, rebuild, reopen, and
cleanup flows.

Real Docker, stock Apple `container` 1.4.1, and the matched
`container-compose` 0.15.1 provider must pass all 18 CLI fixtures and the real VS
Code fixture without normalized semantic differences and with complete timing
evidence. The provider authority is the verified 0.15.1 tag at
`81a2263adf30127a3cf774ffdaf56bd23e2f81c1`. The client fixture pins VS Code 1.137.0, Dev Containers extension
0.470.0, and its embedded Dev Containers CLI 0.89.0.

Immediately before both its CLI and VS Code timed suites, every lane must
produce a quiet-host receipt. The gate waits for the one-minute load to fall
to at most the smaller of one quarter of the logical CPU count and `2.0`,
rejects competing build and Container-family release processes, invalidates
any old success receipt before checking, and retains thermal, load, process,
and policy evidence. A host that does not become quiet within ten minutes
cannot contribute release or optimization timings.

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
change functional parity. A candidate at or above `10.00x` its matching Docker
fixture, a timeout, other non-completion, or missing or invalid timing evidence
fails the parity gate without changing the separately reported functional
result. The full target is in
[PARITY-ROADMAP.md](https://github.com/stephenlclarke/devcontainer/blob/main/PARITY-ROADMAP.md).

See <doc:Performance> for the three-run matrix, variability, phase analysis,
and optimization priorities. See <doc:Conformance> for properties that the
release fixtures do not certify.
