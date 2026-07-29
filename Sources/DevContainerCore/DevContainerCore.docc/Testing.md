# Testing

The hosted-safe suite contains unit, contract, state-recovery, malformed-input,
archive-safety, and HTTP wire tests. Aggregate first-party Swift line coverage
must remain at or above 90 percent. Address Sanitizer and Thread Sanitizer run
in separate clean build directories and fail on crashes or sanitizer findings.
The 1.0.0 release contains 127 Swift tests and records 91.0 percent first-party
line coverage. CI also enforces at least 90 percent
coverage on changed executable lines and retains LCOV plus Sonar generic XML
evidence.

Trusted physical Apple-silicon runners execute the real parity lanes. The
Docker oracle captures normalized results first; stock Apple and the optional
Compose provider must then match those results. A pinned VS Code and Dev
Containers extension perform open, attach, terminal, port, rebuild, reopen, and
cleanup flows.

Real Docker, stock Apple `container` 1.1.0, and the matched
`container-compose` 0.10.1 provider pass all 18 CLI fixtures and the real VS
Code fixture without normalized semantic differences or performance failures.
In the exact 1.0.0 tag run, the largest CLI ratios are 2.876x for stock Apple
and 4.509x for `container-compose`; the corresponding VS Code ratios are
1.232x and 1.311x. Release evidence includes the normalized comparison, raw
recordings, diagnostics, fingerprints, JUnit, and cleanup reports for all
three lanes.

Every CLI fixture also records monotonic wall-clock time in lane JSON and
JUnit. The comparison artifact retains raw durations and reports stock/Docker
and provider/Docker ratios even when functional parity fails. Completed
slowdowns below `10x` are informational; a timeout, other non-completion,
missing or invalid timing, or candidate duration of at least `10x` the matching
Docker fixture fails the parity gate.

See <doc:Performance> for the three-run matrix, variability, phase analysis,
and optimization priorities. See <doc:Conformance> for properties that the
release fixtures do not certify.
