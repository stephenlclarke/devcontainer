# Security policy

## Supported versions

The latest stable minor line is supported. Version 1.0.x receives security
fixes until a newer stable minor line is released. Older lines and Current
development builds may be used to reproduce a report, but they do not receive
separate long-term support.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use
[GitHub private vulnerability reporting](https://github.com/stephenlclarke/devcontainer/security/advisories/new)
to send the affected version or commit, the impact, reproduction steps, and any
suggested remediation.

Reports are handled privately while they are validated. The project aims to
acknowledge a report within three business days and provide an initial
assessment within seven business days. These are response targets rather than
disclosure deadlines. A coordinated disclosure date will be agreed with the
reporter when a fix or mitigation is ready.

## Security-sensitive scope

Reports are especially useful for:

- escaping the local Docker compatibility boundary or gaining unintended host
  access;
- unsafe archive, mount, path, socket, or symlink handling;
- credential or environment leakage into containers, logs, parity evidence, or
  workflow artifacts;
- provider confusion between stock `apple/container` and an explicitly selected
  `container-compose` installation;
- signature, notarization, SBOM, checksum, Homebrew, or release-provenance
  bypasses;
- denial of service, resource leaks, or cleanup failures reachable by an
  untrusted workspace.

The report should avoid including live secrets or unrelated personal data.
Use synthetic credentials in reproductions whenever possible. If a live secret
was exposed, revoke it before submitting the report and state that it has been
rotated.

## Disclosure and fixes

Confirmed vulnerabilities are fixed on a private security fork when practical,
reviewed with a regression test, and published through a GitHub security
advisory. Release artifacts remain subject to the same signed, notarized,
checksummed, and candidate-bound gates described in [RELEASE.md](RELEASE.md);
an urgent security fix does not bypass those integrity controls.

The project will credit reporters who request attribution and will respect a
request for anonymity.
