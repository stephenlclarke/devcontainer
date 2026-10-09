# Issue: Preserve identities created during container inventory

## Problem

Both native and CLI inventory paths read the container list before taking the durable metadata snapshot. Creation can complete while an inventory request is suspended. The newer metadata then includes a container absent from the older native observation, and orphan cleanup deletes its identity. Starting output capture subsequently fails with `Output container identity is no longer present`. The fresh C03 campaign recorded this error during Compose startup.

## Expected behavior

Only metadata present before the native observation is eligible for that observation's orphan cleanup. New identities committed during the request remain available for attachment and output capture. Metadata already present for an actually absent container must still be removed.

## Scope and validation

Capture the cleanup metadata snapshot before the native inventory request in both paths. A deterministic regression inserts a new durable identity while the native list is in flight, without sleeps or live services. It fails before the correction and passes afterward, and also verifies removal of the preexisting orphan. Exact journal incarnation checks and timing deadlines remain unchanged. Fresh packaged C03 qualification is still required to confirm the campaign symptom is resolved.
