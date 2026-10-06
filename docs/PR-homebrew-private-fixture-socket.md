# Make the Homebrew fixture socket private

## Result

The Homebrew test explicitly sets its disposable Unix socket to mode 0600 before invoking the released client. No assertion, production executable, runtime qualification, signed tag or immutable asset changes. The publisher renders the formula from its pinned installation-tool template and records that template checksum in the separately attested operation context; exact-template validation uses the same file.

## Validation and compatibility

The final installed executable check now requires the native package's compiled candidate lane, full source commit and product version. The stable/current distribution context remains separately checked. A regression rejects substituting the distribution lane for the compiled candidate identity. This follows the existing native archive smoke contract and permits promotion of exactly the already qualified bytes.

Run the installation-tool and immutable-resumption tests, workflow and Markdown checks. Execute the corrected actual formula against the unchanged installed package and require strict baseline restoration. This fixes default Homebrew test execution as well as the publication check, without weakening socket security or relying on an ambient umask.
