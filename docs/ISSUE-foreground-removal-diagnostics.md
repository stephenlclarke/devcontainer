# Foreground auto-removal failures lack bounded state evidence

## Problem

A foreground exit test can fail its immediate identity or state check after the acknowledged exact exit. The existing HTTP receipts omit inspect bodies, so they cannot distinguish an identity change from an unexpected transient state.

## Expected behavior

Retain failure-only private evidence with lookup kind, identity-match booleans and bounded process state. Keep inspect payloads, IDs, names, images, commands and labels out of the diagnostic. Preserve all identity, state and deadline checks.

## Validation

Focused tests cover identity drift and unexpected running state without sensitive identity values in the diagnostic. This adds diagnosis only and does not qualify or waive a failed fixture.
