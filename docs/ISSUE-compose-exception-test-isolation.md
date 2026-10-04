# Issue: isolate the operation expected to fail in Compose tests

## Problem

The authoritative SonarCloud analysis for `35269107932ac8775e97d84f6554d7cd54e2f1fe` reports three `python:S5778` findings. Each affected exception assertion constructs a guest fixture inside the assertion, so a fixture-construction exception could be mistaken for the intended readiness failure.

## Required outcome

Construct each guest before entering the exception assertion. Keep the expected exception, readiness inputs, deadline sequence and journal checks unchanged. Require focused tests and a fresh authoritative SonarCloud analysis before release.
