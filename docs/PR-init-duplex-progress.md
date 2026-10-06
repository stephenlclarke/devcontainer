# Pull request handoff: test(parity): retain positive duplex progress timing

Extend only the existing shared duplex evidence dictionary with first/last elapsed monotonic times and counts for successful socket reads/writes. Existing byte counts and EOF flags are preserved. No bytes are added to retained diagnostics, and the original connection deadline remains unchanged.

This distinguishes recent positive host-socket progress from a stationary stall in fresh E07 evidence; it does not identify guest consumption or erase the c954 failure. Existing Unix socket tests preserve strict count/EOF semantics, and a delayed positive-read regression verifies timings and the same bounded timeout. Root tests-only execution with canonical SSD scratch reproduced missing progress fields; the corrected harness passes all 40 checks. Repository formatting, lint and parity manifest checks pass; fresh signed diagnostics remain pending; the external worker did not run tests or runtime operations.
