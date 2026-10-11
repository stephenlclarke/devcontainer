# Issue: split notary recovery test module-loader assertions

## Motivation and required behaviour

The exact-main Sonar analysis for source `bed73cec07a242df282811f5b7ce3508ff0a5e16` reports python:S9073 at `Tools/release/test_recover_notary_profile.py:23`. The loader precondition combines two independent assertions. Split them so the static analysis passes while preserving the current module-loading behavior.

## Scope and validation

Change only the test loader assertions and issue/PR handoff documentation. Run the focused notary profile recovery tests. Production behavior, release gates, credentials, and packaged bytes remain unchanged.

## References

See [the correction handoff](PR-93.md) and [issue 92](https://github.com/stephenlclarke/devcontainer/issues/92).
