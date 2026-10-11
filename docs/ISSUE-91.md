# Issue for PR 91: verify the configured notary account before profile recovery

## Motivation and required behaviour

The existing recovery workflow validates the team ID but only checks that the Apple ID secret is nonempty. A rejected credential must be diagnosed against the intended Apple account before any profile write. GitHub does not expose secret values through its secret-listing API, so verification must happen on the designated release runner without revealing the value.

## Acceptance criteria

- Provide a manual workflow restricted to the exact protected-main control SHA, the `release` environment, and the designated macOS ARM64 runner.
- Validate repository, ref, SHA, runner name, machine architecture, account, UID, and hostname before accessing the Apple ID and team secrets.
- Compare the UTF-8 SHA-256 digests of both values with the fixed expected identities and print only fixed match or mismatch statuses.
- Do not check out source, read or write a keychain, invoke `security` or `notarytool`, access the network, submit an archive, or change release artifacts.

## Validation

Use actionlint and Markdown lint, then exercise the inline comparison code with harmless values in a temporary harness covering matches, mismatches, missing values, and whitespace changes. Confirm test output never contains the input values. Dispatch remains a separate operator action after merge to protected `main`.

Implementation: [PR 91](https://github.com/stephenlclarke/devcontainer/pull/91).
