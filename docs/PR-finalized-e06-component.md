# Pull request handoff: test(parity): support phased E06 component checks

Extend the existing finalized-package component allowlist with E06 network/volume while retaining E13 Compose signals. Pass the actual selected fixture through comparator validation, receipt identity and errors; reuse existing filtering, suite lifecycle, exact cleanup and initialized evidence checks. Full campaign and qualification sealing/verifier behavior remain unchanged.

Add focused offline E06 regressions for exact filtering, CLI-only execution, comparison differences/extra fixtures, incomplete cleanup, dynamic nonauthoritative receipts and evidence rechecks. Existing E13 regressions are preserved. No workflow, fixture semantics, benchmark or product behavior is added.

Run `PYTHONPATH=Tools/parity python3 -m unittest test_qualify_finalized_package.E06ComponentTests test_qualify_finalized_package.SuiteLifecycleTests` after applying the proposal. The actual unchanged-source focused run produced the expected three failures and three errors in the six new E06 cases. After applying the change, all 30 focused cases and all 64 qualifier-module cases pass. A freshly signed selected component provides early feedback only; the complete 84-cell release campaign remains mandatory.

The existing `native-parity-component` Make target defaults to E13 only within its recursive recipe. Select E06 with `make native-parity-component NATIVE_PARITY_COMPONENT_FIXTURE=E06-network-volume` and the same explicit finalized-package inputs. No global fixture default is introduced; `native-parity-release` remains full mode without an explicit component selector. Offline dry-run assertions cover E06, the E13 target default and unchanged full mode without running the controller.
