# Issue 67: pin the native init wait lifecycle fix

## Problem description

The 0.12.0 matched stack requires Container's native init-process wait ordering
fix. Devcontainer still resolved the previous Container release candidate and
would therefore describe a different lifecycle authority from Container
Compose.

## Resolution

The package manifest and lockfile now pin Container main at
`d28f51eeb5a5cc0189b4c29e5dd7e40ac322ee04`.

## Focused evidence

- The resolved package graph reports the exact Container revision.
- Container's focused native wait regression and matched Compose lifecycle
  helper sequence pass at this revision.

Refs [#67](https://github.com/stephenlclarke/devcontainer/issues/67).
