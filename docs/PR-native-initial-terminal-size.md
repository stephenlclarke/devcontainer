# PR: preserve Docker create-time TTY dimensions on Apple

## Summary

Carry Docker `HostConfig.ConsoleSize` through container metadata and inspect, then apply non-default dimensions before the original workload starts. The Bazel package supplies an architecture-matched static Linux launcher whose expected hash is embedded in the signed engine. Native creation mounts that helper read-only at a fixed guest path; stock Apple remains unmodified.

`[0, 0]` and non-TTY requests retain native behavior. Partial-zero axes remain preserved, and the launcher keeps the original command, arguments, environment, user, and working directory. Create and restart paths verify the installed helper identity and the attested workload configuration.

## Focused coverage

- Docker create/inspect dimensions, default and non-TTY behavior, malformed
  values, and legacy `ContainerSpec` decoding.
- Launcher identity admission, process projection and attestation, partial
  zero dimensions, and conflicting mount rejection.
- Enhanced process policy is applied before the process attestation is made.

Root-owned Bazel invocation `4c22f481-5f55-41dc-a5c9-bd86d6f48242` passes compilation and all four focused targets at the development snapshot. The unchanged E15 first-instruction fixture and complete 84-cell campaign have not been run, so this change does not establish release parity.
