# Issue: include notarization recovery in the runner inventory

## Problem

The main CI source validation fails because its runner inventory test expects three self-hosted jobs. The maintained notarization recovery workflow adds a fourth designated-MBP job.

## Expected behavior

Validate every self-hosted job uses the designated MBP, and assert the exact supported workflow/job inventory so additions and removals remain reviewable.

## Evidence

Main CI run 38142379003 failed with `AssertionError: 4 != 3`. All four jobs already select `devcontainer-designated-mbp`; runner routing is unchanged.

## Scope

Update the test inventory only. No release or credential admission is relaxed.

Tracked in [issue 98](https://github.com/stephenlclarke/devcontainer/issues/98).
