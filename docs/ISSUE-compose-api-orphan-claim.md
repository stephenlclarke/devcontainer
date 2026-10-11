# Failed native container creation leaves an empty Docker API project claim

## Problem

When a first `container-compose` native Docker API container create fails before the runtime creates a resource, the gateway records the mutation as failed but leaves the newly created `uid:docker-api` project claim in state. Later provider selection sees that empty stale claim and blocks recovery or a clean retry. The current recovery rule must continue retaining projects that existed before the failed request or that own tracked resources.

The regression is isolated to a request that passes API preflight, then fails before native creation; a preflight rejection does not reproduce the failed mutation state. A separate ambiguous-outcome case creates a native container and loses the response before Docker API journaling, and must retain the failed claim for recovery.

## Expected behavior

If a `container-compose` Docker API container-create mutation creates the project claim and then fails, remove that new empty claim only when a read-only runtime inventory finds no request-owned native container and the runtime confirms there is no pending create intent. Preserve an existing claim and its recovery operations, and preserve any claim with a tracked resource or uncertain native create outcome.

## Scope

Change only project-mutation failure cleanup, deterministic Docker API regression coverage, and the matching README/issue/PR handoff. Do not change provider selection, generated Compose API behavior, or unrelated project recovery semantics.

## Regression evidence

The new Docker API regression was confirmed red before the cleanup fix in invocation `466242a3-2435-4c66-b0a7-7057e0eddbf8`; the only failure was the assertion that the newly created empty claim should be absent. The accepted focused-test results are recorded in the matching [PR handoff](PR-compose-api-orphan-claim.md). These unit tests do not establish live runtime or campaign qualification.
