# Issue: Bind native candidate diagnostics to their admitted package

## Motivation

The native-only diagnostic controller admits an unsigned schema-2 candidate, but its active-provider HOME guard accepted only finalized-package scopes and compared the guard source to finalized-package identity. Candidate C03, D05 and E07 diagnostics therefore failed during owned guest preparation before Engine startup.

## Expected behavior

The guard should admit only the exact candidate diagnostic transaction already accepted by package admission. It must bind the campaign, source, candidate invocation, receipt digest, archive digest, stock profile, fixture selection and evidence root. Existing finalized full and component scopes must keep their current admission rules.

## Risk

Allowing a scope name without checking the active candidate identity could let a guard from another candidate or fixture set authorize guest provisioning.
