# Accept the authenticated seven-executable candidate in guest fixtures

<!-- markdownlint-disable MD013 -->

`fixture_guest_inputs` now recognizes either the legacy five-executable candidate or the complete schema 2 seven-executable candidate. The latter requires both terminal-launcher receipt hashes and the Go SDK licence hash already verified by `prepare_candidate.admit_candidate`. Partial launcher sets, extra names, malformed hashes and stale proof fail before runtime selection. Candidate archive bytes, source/profile identity and scope checks are unchanged.

The focused regression failed on the original exact-five guard and passes with the correction. The current candidate's D05 live case still needs a fresh maintained run; this unit proof does not claim guest parity.
