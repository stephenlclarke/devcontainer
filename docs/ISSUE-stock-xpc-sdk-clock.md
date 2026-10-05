# Issue: signed package crashes in the stock XPC timeout child

## Motivation

The complete 0427 signed-package runtime campaign passed the Docker lane but its stock Engine crashed before the first fixture. The retained diagnostic points to Swift task deallocation in `XPCClient.send`'s generic timeout sleep. Changing the application event retry sleep did not prevent the crash. The anonymous-XPC focused test passed on the old SDK, so that test does not reproduce the signed-package failure.

## Required behaviour

Preserve the stock Apple runtime and its dependency versions. Compile the stock-facing SDK from the minimal Apple 1.4.1 derivative whose only production change uses a direct ContinuousClock deadline. Publish and verify the new SDK asset before committing its release lock; reuse only authenticated unchanged lower assets.

## Validation and scope

The signed-package failure is retained as the failing integration result. Anonymous XPC fast-reply and closed-connection tests exercise teardown without starting host services. Exact source-graph and layer-admission positive and negative controls must pass. Final package runtime parity, signing, notarization and GitHub release authority remain required.

Linked implementation: [SDK correction](PR-stock-xpc-sdk-clock.md).
