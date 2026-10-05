# Pull request handoff: test(parity): prepare authenticated guest inputs for isolated E06

Reuse the existing owned-guest input admission and native provisioning path when the selected fixture is exactly E06 network/volume on a native lane. E06 continues through its original legacy Engine probe with every assertion unchanged. Existing Docker and full-campaign fixture selection remain unchanged. The same selected private HOME, kernel/image identities, journal and cleanup fences apply.

The original signed 455 component failed in both native lanes because no default arm64 kernel had been configured; Docker passed. Offline regressions cover both native preparation orders, fail-closed provisioning, API-only single provisioning, and Docker/full selection. The 16-case focused run failed against unchanged 455 source with three failures and one error, then passed all 16 after the correction. Fresh signed component proof remains required; the original failed evidence remains preserved.

After application, run `PYTHONPATH=Tools/parity python3 -m unittest test_run_lane.ComponentBuilderSelectionTests test_run_lane.EngineRoutePreflightTests test_owned_guest_fixture.OwnedGuestFailureTests`. The focused and complete parity harness checks are executed locally before pushing. Component success does not supply full release authority.
