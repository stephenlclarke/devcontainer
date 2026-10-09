# Pull request handoff: fix(parity): bound E07 native init I/O diagnostics

The E07 native diagnostic can now opt into payload-free stream tracing with `--trace-init-io`; the option is accepted only for the single E07 native diagnostic, forwarded explicitly into the admitted lane runner and recorded in operator inputs as diagnostic-only timing. Each process generation limits progress records to 256 while retaining synchronized final input/output totals, EOF state, process exit and drain result. Logging writes are serialized, and disabled tracing avoids counter updates.

The native diagnostic controller now runs the second provider after a first-provider functional failure only when initial native API preflight succeeded and the first lane reports both `restored` and `cliCleanupComplete: true`. It preserves the functional failure in the final status. Failed preflight, uncertain restoration and incomplete CLI cleanup still stop execution. Full qualification ordering and behavior are unchanged.

Validation: `python3 -m unittest test_qualify_finalized_package test_run_lane` passes all 151 tests. `Tools/bazel/run.sh test --config=stock //:DevContainerAppleRuntimeTests` passes. SwiftFormat lint passes. No live runtime or release qualification was run; traced timing remains diagnostic evidence only.

See the [issue handoff](ISSUE-e07-native-io-tracing.md) for the symptom and intended diagnostic boundary.
