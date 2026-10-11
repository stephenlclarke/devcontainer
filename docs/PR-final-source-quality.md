# PR: correct final-source analysis while retaining transport semantics

## Implementation

The failed-upgrade collector now explains its intentional empty callback with a nested line comment: diagnostics stay bounded inside the collector and never reach raw output. Duplex writing moves the synchronous byte loop into a private helper while keeping the same write lock and duplicated descriptor around it. Cancellation/deadline checks, EINTR handling, empty writes and worker closure remain unchanged.

The SwiftPM test loader has separate precondition assertions. Four exception tests construct recovery objects or response payloads before their exception contexts, so only the operation under test can satisfy the expected exception. Cases and assertions remain unchanged.

## Guarded launcher analysis

Sonar S131 at `Tools/bazel/run.sh` lines 96 and 111 ignores the enclosing finite-domain precondition. `protected_define_key` lines 76-80 admits only `DEVCONTAINER_COMMIT`, `DEVCONTAINER_BUILD_LANE`, `DEVCONTAINER_SOURCE_DIRTY` and `runtime_profile`, all handled by both nested cases. Independent review confirms default is unreachable. `Tools/bazel/test_launcher.py` tests every protected key in joined/split `--define` and `-D` forms, plus unknown-key passthrough. These two exact findings receive evidence-specific false-positive dispositions; no rule-wide exclusion or code-quality waiver is introduced. Future key additions must update both cases and the matrix. Dependency recipes and released bytes remain unchanged.

## Validation and remaining gates

Focused Python validation passes: 21 SwiftPM-preparation tests, 49 build-image/probe tests with canonical `/private/tmp`, and the two protected-flag tests including the complete spelling matrix. The unchanged ambient temporary-directory attempt is retained as unqualified fixture-setup feedback. Both native Docker-client profiles pass all 99 test functions across 11 suites; stock instrumented coverage is 1789/1858 module lines (96.3%) and 595/641 transport lines (92.8%). An enhanced post-test origin check initially observed an unused Foundation source overlay; resolving the exact authenticated archive metadata file targets restored correct origin admission, and the admitted component run passes. No compilation or recipe change was needed for that metadata refresh. Focused strict Swift formatting/lint, Markdown lint and the independent transport review pass. A fresh exact-head Sonar analysis must confirm the corrected source and reviewed dispositions. These focused checks do not qualify the final merged-main package, live parity or release.

Linked issue: [final-source quality](ISSUE-final-source-quality.md). Review: [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).

## Candidate diagnostic test findings

The later PR 88 scan reported four findings in two Python test files. Candidate-selection arguments and the feature-cache timestamp now precede their exception contexts; each context contains only the operation under test. The cache-proof boolean assertion uses `assertTrue`. All 66 candidate-lane and nine feature-cache tests pass with their original assertions and error-message checks preserved. This change does not alter product code, dependency recipes, archived releases or timing gates. The failed hosted analysis remains recorded; a fresh exact-head analysis must confirm the fixes.

The signing fixture now explicitly assigns its synthetic Go license the required `0644` mode, matching its existing executable mode declarations. The original 25 failures and 12 errors under `umask 077` remain retained. Focused signing tests and complete source validation run under the same private umask; production mode checks and signing behavior are unchanged.

Git patch application, proof construction and reverse application now use a child-only `022` umask so recreated files retain their reviewed modes. The caller's umask stays private, and all other subprocesses retain its permissions. No checksum, mode, origin or rollback admission check was relaxed. The synthetic checkout explicitly declares its canonical tracked-file mode. A new regression fails against the previous implementation and passes against the fix, checking first application, exact repeated admission, all three tracked-file modes and unchanged caller permissions. All 25 preparation tests pass under `077`; the nine original preparation errors remain retained.
