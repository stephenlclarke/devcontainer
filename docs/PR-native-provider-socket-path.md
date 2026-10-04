# Pull request handoff: bound native provider socket paths

## Change

The preflight and parity scratch names are `api-{lane}-` and `par-{lane}-`. Global executed-run admission checks both layouts for both native providers; each allocated root is checked again before host mutation. The guard counts filesystem-encoded UTF-8 bytes and permits at most 103 pathname bytes. A parity root rejected at allocation is removed with an exact empty-directory removal, without touching any service or broader scratch tree.

## Validation

Six focused tests pass, including exact 103-byte acceptance and 104-byte rejection, non-ASCII byte boundaries, the default SSD root plus `/abc`, the old parity overflow, preflight failure before Keychain/service creation, and parity failure before runtime construction. No native service was launched by these tests and no live qualification is claimed.

## Compatibility and remaining work

Provider binaries, release pins, fixture workloads and cleanup contracts stay unchanged. The old failed attempt remains retained. A new exact-source signed-package campaign must establish host/runtime success.

Related to [pull request 83](https://github.com/stephenlclarke/devcontainer/pull/83) and [the matching issue handoff](ISSUE-native-provider-socket-path.md).
