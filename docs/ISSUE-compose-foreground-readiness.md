# Issue: Compose foreground readiness before native start commit

## Problem

In the retained finalized-package E13 attempt, guest stdout arrived and the first Docker inspection returned a real container that was not yet `running`. The fixture failed immediately with `Compose foreground output has no running guest`. Cleanup later forwarded TERM to that guest and observed its exit status 23, so the first inspection did not establish that startup had failed.

External Compose attaches and drains output before its `/start` request returns. The native runtime starts the process before committing the public `running` snapshot. Devcontainer projects that pre-start snapshot as `created`. The fixture treated stdout as proof that this later state commit had already finished.

## Required outcome

Retain the first verified immutable guest ID for cleanup, then require an authentic `running` inspection of that same owned ID within the existing 45-second operation deadline. Preserve failure for a changed identity, exited CLI, unexpected output, invalid state or missed deadline. The old failed campaign remains failed; a new live E13 result is still required.

Tracked with [pull request 83](https://github.com/stephenlclarke/devcontainer/pull/83).
