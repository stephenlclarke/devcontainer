# Issue: Keep finalized-package admission directory modes stable

## Problem

`qualify_finalized_package.py` sets the private umask only after `admit_package_before_runtime`. Admission extracts the signed package first. With a normal caller umask of `022`, archive directories are created as `0755`; the retained receipt records those modes and differs from the existing private `0700` inventory. A fresh SSD admission then fails even though the package files and hashes are unchanged.

## Expected behavior

An executing qualification must set umask `0077` before package admission creates any extracted tree, so generated directories match the retained inventory and existing receipt.

## Scope and validation

This issue is limited to admission umask ordering. It does not change fixture behavior, parity expectations, release identity checks, or benchmark methods. The regression test builds a real tar fixture, seeds a retained receipt under `0077`, then invokes the qualification entry point under caller umask `0022` and exercises the real maintained `prepare_releases.prepare` extraction. It checks directory modes and receipt bytes. The test fails against current source with `Cannot replace a different retained preparation receipt` and passes against the staged patch.
