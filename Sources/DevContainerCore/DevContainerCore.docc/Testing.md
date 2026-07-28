# Testing

The hosted-safe suite contains unit, contract, state-recovery, malformed-input,
archive-safety, and HTTP wire tests. Aggregate first-party Swift line coverage
must remain at or above 90 percent. Address Sanitizer and Thread Sanitizer run
in separate clean build directories and fail on crashes or sanitizer findings.
The current 1.0.0 candidate contains 123 Swift tests and records more than 90
percent first-party line coverage. CI also enforces at least 90 percent
coverage on changed executable lines and retains LCOV plus Sonar generic XML
evidence.

Trusted physical Apple-silicon runners execute the real parity lanes. The
Docker oracle captures normalized results first; stock Apple and the optional
Compose provider must then match those results. A pinned VS Code and Dev
Containers extension perform open, attach, terminal, port, rebuild, reopen, and
cleanup flows.

Real Docker currently passes all 18 CLI fixtures. The latest stock Apple
`container` 1.1.0 and matched `container-compose` provider candidate runs pass
17 of 18 while Local Network permission for Apple's signed runtime helper
remains disabled on the physical runner. A stable release requires those lanes
and the real VS Code fixture to pass without semantic differences. Release
evidence includes the normalized comparison, raw recordings, diagnostics,
fingerprints, JUnit, and cleanup reports for all three lanes.
