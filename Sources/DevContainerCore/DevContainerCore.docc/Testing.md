# Testing

The hosted-safe suite contains unit, contract, state-recovery, malformed-input,
archive-safety, and HTTP wire tests. Aggregate first-party Swift line coverage
must remain at or above 90 percent. Address Sanitizer and Thread Sanitizer run
in separate clean build directories and fail on crashes or sanitizer findings.
The 1.0.0 release suite contains 121 Swift tests and records more than 90 percent
first-party line coverage. CI also enforces at least 90 percent coverage on
changed executable lines and retains LCOV plus Sonar generic XML evidence.

Trusted physical Apple-silicon runners execute the real parity lanes. The
Docker oracle captures normalized results first; stock Apple and the optional
Compose provider must then match those results. A pinned VS Code and Dev
Containers extension perform open, attach, terminal, port, rebuild, reopen, and
cleanup flows.

All 18 CLI fixtures and the real VS Code fixture pass against real Docker,
stock Apple `container` 1.1.0, and the matched Apple Compose stack. Release
evidence includes the normalized comparison, raw recordings, diagnostics,
fingerprints, JUnit, and cleanup reports for all three lanes.
