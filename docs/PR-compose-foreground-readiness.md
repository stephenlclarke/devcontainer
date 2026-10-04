# Pull request handoff: authenticate Compose foreground readiness

## Change

The foreground fixture journals the first fully owned container ID while it is `created`, re-inspects that immutable ID, and proceeds only after an actual `running` response. Each inspection receives the remaining original deadline as its total HTTP budget. The deadline and CLI liveness are checked again after the final inspection receipt is durable, before returning success. The existing cleanup can reconcile a validated created guest even when readiness fails.

## Validation

The isolated exact-source fake-backend suite passes 76 tests across the foreground and guest-fixture modules. It covers created-to-running, ownership and ID replacement, disappearance, CLI exit, unexpected output, deadline expiry including a late running response, and cleanup after failed readiness. No live runtime or E13 campaign was run for this patch.

## Compatibility and remaining work

The guest command, Compose provider, signal contract, 45-second deadline and running-state requirement are unchanged. The retained failed E13 evidence is historical. The complete signed-package parity campaign must be rerun on a reviewed new source head before claiming qualification.

Related to [pull request 83](https://github.com/stephenlclarke/devcontainer/pull/83) and [the matching issue handoff](ISSUE-compose-foreground-readiness.md).
