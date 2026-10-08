# Clarify the Homebrew installation failure assertion

## Result

The foreign-repository regression now creates its installation transaction before `assertRaises` and invokes only `transaction.run()` inside the context. The expected error and post-failure ownership checks are unchanged.

## Compatibility and validation

This test-only change does not alter Homebrew behavior, package contents or release evidence. The focused `Tools/release/test_homebrew_installation.py` module validates the existing rejection and cleanup behavior.
