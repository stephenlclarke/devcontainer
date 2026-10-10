# Recover the persistent notary profile without running a release

The release runner's profile can become unavailable even while the repository's notarization secrets remain configured. The existing package workflow is unsuitable for recovery because it builds and publishes release assets.

This change adds a narrowly scoped manual workflow for the exact protected-main commit on the designated runner. A small helper verifies the expected repository, ref, control SHA, runner name, local account and UID, host, and login-keychain owner before it uses the secrets. It stores and validates the configured `container-only-unattended` profile for team `4MEB7MUTAV` in the persistent login keychain, then checks notarization history against that exact keychain. The helper closes stdin, applies command deadlines, suppresses command output, and does not unlock the keychain.

The workflow has read-only repository permissions, uses the protected `release` environment, and runs no build, signing, submission, stapling, artifact upload, or publication steps. It does not alter release assets or retry existing submissions.

Validation is limited to focused mocked tests and workflow structure checks. Dispatch remains an explicit operator action after merge to protected `main`; no workflow was dispatched as part of this change.
