# PR: release empty claims after failed native Docker API container creation

## Summary

When a `container-compose` Docker API `POST /containers/create` request creates a new project claim and then fails, the coordinator removes that new empty claim only after the runtime confirms there is no request-owned native container and no pending write-ahead create intent. If either check is unavailable or inconclusive, the failed claim remains for recovery. Existing project claims and their unfinished recovery operations remain available; successful resource-owning mutations keep their existing lifecycle.

## Linked issue

Fixes [the failed-create project-claim issue](ISSUE-compose-api-orphan-claim.md). No external issue number has been assigned yet.

## Design and compatibility

The coordinator records whether the claim existed before the mutation while holding the per-project mutation lock. Failure cleanup checks that the mutation is the native Docker API container-create route, requests empty-project release, originated from a new claim, and still owns no recorded resources. The router also requires a runtime creation-intent probe and an inventory scan that finds no container bearing this request's operation identity; an explicitly requested name is checked too. Missing probe support, pending intent, inventory errors, and visible native containers all retain the failed claim. The Apple runtime probe reads durable pending-create records without reconciling or clearing them. The state store's existing release operation enforces the empty-resource guard transactionally. Docker API request and response shapes, provider selection, Compose CLI behavior, and stock-backend failure retention do not change.

## Validation

The before-fix functional red invocation `466242a3-2435-4c66-b0a7-7057e0eddbf8` failed only the new assertion that the empty new claim should be released. An uncached focused target run passed 328 Apple runtime tests across 39 suites and 144 Docker API tests across 16 suites, although the broader source-identity admission rejected that invocation because unrelated parity-helper edits were present. Stable-identity cached replay `e77b9789-4958-47c5-b56e-94d50082bf62` accepted the exact Swift outputs for both targets with a valid source guard, both passing. The focused Bazel targets are `//:DevContainerDockerAPITests` and `//:DevContainerAppleRuntimeTests`.

These component tests do not establish native runtime parity, a full campaign, package qualification, release, or publication. The C03 campaign has since finished and its guard is clear; no runtime or release action was part of this fix.

## Remaining risks

The read-only absence proof depends on the runtime's list operation and durable intent probe. Providers that cannot supply the intent probe retain the failed project claim. Full live partial-create recovery qualification remains outstanding.
