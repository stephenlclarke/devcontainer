# Testing

The hosted-safe suite contains unit, contract, state-recovery, malformed-input,
archive-safety, and HTTP wire tests. Aggregate first-party Swift line coverage
must remain at or above 90 percent. Address Sanitizer and Thread Sanitizer run
in separate clean build directories.

Trusted physical Apple-silicon runners execute the real parity lanes. The
Docker oracle captures normalized results first; stock Apple and the optional
Compose provider must then match those results. A pinned VS Code and Dev
Containers extension perform open, attach, terminal, port, rebuild, reopen, and
cleanup flows.
