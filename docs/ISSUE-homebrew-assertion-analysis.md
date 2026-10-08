# Homebrew installation assertion analysis

## Problem

Sonar reports Python rule S5778 at the foreign-repository rejection test because `self.transaction().run()` constructs and executes the transaction inside `assertRaises`. The expression obscures whether additional operations might raise the expected exception.

## Required result

Construct the transaction before entering the assertion context and keep only `transaction.run()` inside it. Preserve the existing error assertion and state-restoration checks.

## Validation

Run the focused Homebrew installation test module and confirm the changed test keeps its existing failure and cleanup assertions.
