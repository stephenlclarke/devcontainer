# Fix: configure locking on the disposable qualification keychain

## Motivation

A locked private keychain interrupted finalized runtime qualification.

## Implementation

The creation helper sets no sleep locking and the Security API's no-timeout interval on the newly created reference, reads those settings back and verifies the same canonical keychain remains unlocked. The helper never changes the default keychain or global search list. The random private password remains in memory.

## Validation

Sixteen focused tests cover settings and path failures. The original helper fails the new regression, and a real owned create/settings/delete smoke passes. Full release qualification remains pending.

## Compatibility and risks

Only disposable qualification keychains receive these settings. Cleanup retains exact ownership checks. Related issue: [disposable keychain settings](ISSUE-disposable-keychain-settings.md).
