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
Compose provider must then match those results. A pinned VS Code and Dev
Containers extension perform open, attach, terminal, port, rebuild, reopen, and
cleanup flows.

Real Docker, stock Apple `container` 1.1.0, and the matched
`container-compose` 0.10.1 provider pass all 18 CLI fixtures and the real VS
Code fixture without normalized semantic differences and with complete timing
evidence.
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
