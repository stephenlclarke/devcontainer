# Issue: disposable qualification keychain can lock during a campaign

## Problem description

The D26 campaign's private qualification keychain was locked when inspected after engine startup failed. Its creation helper did not explicitly configure locking policy. The observation establishes the locked state, not the precise cause or timing of the lock.

## Required behavior

Configure and read back locking settings through the exact newly created private keychain reference. Leave the operator's default keychain, search list and locking settings unchanged. Reject mismatched paths, settings or unlocked state before reporting successful creation.

## Validation and remaining work

Sixteen focused tests pass. Restoring the original helper fails the settings regressions. A real private create/settings/delete smoke passed and restored its host guard. This smoke does not establish long-duration campaign success; fresh full release qualification remains required.
