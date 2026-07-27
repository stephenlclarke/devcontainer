# Testing

The hosted-safe suite contains unit, contract, state-recovery, malformed-input,
archive-safety, and HTTP wire tests. Aggregate first-party Swift line coverage
must remain at or above 90 percent. Address Sanitizer and Thread Sanitizer run
in separate clean build directories. The current suite contains 75 Swift tests
and records 90.50 percent first-party line coverage.

Trusted physical Apple-silicon runners execute the real parity lanes. The
Docker oracle captures normalized results first; stock Apple and the optional
Compose provider must then match those results. A pinned VS Code and Dev
Containers extension perform open, attach, terminal, port, rebuild, reopen, and
cleanup flows.

All 18 CLI fixtures currently pass against real Docker and Stephen's matched
Apple Compose stack. The pinned real VS Code fixture passes against Docker.
Stock-Apple and Apple-Compose VS Code recordings remain required for a stable
release.
