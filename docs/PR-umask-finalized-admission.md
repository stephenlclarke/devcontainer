# PR: Set the private umask before finalized-package admission

## Motivation

Signed-package extraction ran before the private umask was set. A caller using `0022` therefore created directory modes that differed from the retained `0700` preparation receipt, preventing a fresh SSD admission of the same package.

## Implementation

Set `os.umask(0o077)` after inert preflight and before admission. Package bytes, signatures, provenance checks, fixtures and timing methods remain unchanged. Inert preflight does not alter the process umask.

## Validation

`python3 -m unittest discover -s Tools/parity -p test_finalized_admission_umask.py` passes. The regression extracts a real synthetic archive through maintained preparation code, seeds a retained receipt under `0077`, and enters qualification from `0022`; directory modes and receipt bytes remain identical. The same regression reproduced the retained-receipt mismatch before the fix. Normal `make lint` discovery includes it. The complete parity tooling suite passed: 249 tests.

## Compatibility and risks

This affects package-admission orchestration only. It was applied after all fresh timed campaigns ended; those observations continue to identify the original measured harness. Fixture failures and the raw comparison timing failure are preserved separately.

## Links

See [the issue](ISSUE-umask-finalized-admission.md). No product rebuild or new notarization is required for this controller correction.
