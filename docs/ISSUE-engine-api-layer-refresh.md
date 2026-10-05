# Issue: refresh Engine API and Container SDK dependency layers

## Motivation

The released Engine API transport fails a slow 32 MiB raw-exec upload with a broken pipe. Correcting the Engine API source requires advancing both profile pins and the enhanced Container nested dependency. The existing finite recipe compatibility check accepts only the previous exact source transition, so an unrestricted pin change would also reject unchanged Foundation and Containerization archives.

## Required behaviour

Bind the new Engine API stock and enhanced sources and enhanced Container source to one reviewed manifest and two resolved locks. Reuse only the four unchanged Foundation and Containerization canonical locks, retaining their exact source, lower archive, sidecar, recipe, shared build snapshot and toolchain checks. Newly publish Engine API and Container SDK in both profiles; reject their old archives under the new transition.

## Scope and validation

Change only the existing excluded compatibility verifier bodies, focused tests and release documentation. Preserve the production AST, importer, producer, shared BUILD/module recipe and immutable historical fixtures. Cover accepted lower reuse and rejected pin, manifest, lock, location, asset, toolchain and recipe drift. Download and admit new assets through the maintained release helpers before committing each pair of locks. Dependency admission remains separate from full stable product qualification.

Linked implementation: [layer refresh handoff](PR-engine-api-layer-refresh.md).
