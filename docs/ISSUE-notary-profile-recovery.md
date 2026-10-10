# Restore the designated runner's notary profile

## Problem

The release runner's persistent notary profile is unavailable or has invalid credentials, while the repository's three notary secrets remain configured. The normal release workflow uses an operation-scoped keychain and is not an appropriate credential-recovery path because it also builds and publishes release assets.

## Acceptance criteria

- Provide a manually dispatched workflow restricted to the exact protected `main` control SHA, the release environment, and the designated self-hosted macOS ARM64 runner.
- Confirm the runner account, UID, host, default login keychain, repository, branch, and expected control SHA before storing credentials.
- Store and validate the existing repository notary secrets under the configured `container-only-unattended` profile in the runner user's login keychain, requiring team ID `4MEB7MUTAV`, then run noninteractive `notarytool history` against that same profile and keychain.
- Close stdin, bound both notarization commands, suppress all command output that could contain credentials or submission history, and fail without unlocking the keychain or exposing secrets in logs or artifacts.
- Do not build, sign, submit, staple, publish, mutate release assets, or retry any existing submission.

## Validation

Use focused unit tests with mocked command execution to cover dispatch and host restrictions, missing or partial secrets, failed credential validation, malformed history output, output redaction, and the absence of release side effects. The workflow itself is dispatched only after its exact control commit is merged to protected `main` and an operator invokes it.
