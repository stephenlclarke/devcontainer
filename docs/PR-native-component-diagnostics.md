# fix(parity): align focused native admission and retain inspect conflicts

Both the qualifier and private provider HOME admission accept exact E06/E07/E13/E14 singleton selections. E14 runs without an unrelated builder, retaining all seven terminal observations and exact comparison. Duplicate, mixed, unknown, foreign-source and wrong-scope selections remain rejected.

Valid message-only inspect HTTP 409 JSON of at most 64 KiB is retained in the private service journal. Events contain only entry name, checksum and count; diagnostic messages do not enter public errors or exported payloads. Success and 404 behavior remain unchanged. Native production behavior and test deadlines are unchanged.

Tests-only root execution reproduced the missing admission, private diagnostic and E14 selection failures. All 81 focused admission, comparison and Unix-socket checks pass. The shared guest regressions exposed an outdated mocked call expectation; the updated expectation preserves the original total HTTP budget. Fresh signed runtime diagnostics remain required; no passing component is release authority. Repository formatting, lint and immutable parity-input validation pass. The shared suite passed 93 checks, and its corrected budget expectation passes separately. The earlier failed campaigns remain failed.
