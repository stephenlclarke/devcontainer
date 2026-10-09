# Identify the stalled phase in bounded E07 attachment failures

## Problem

The first two fresh stock Apple trials failed E07 with `Exec stream exceeded its whole-connection deadline` after 50.779s and 48.879s. They had no semantic differences, but the sealed fixture journal retained only a digest receipt; the result had no stage or socket-progress details. The current probe uses one 30-second deadline for each attachment transfer, so the failure could be in connect, upgrade, native start, startup observation, duplex, or combined history/live observation. The retained rows cannot distinguish those phases. Do not conflate them with the earlier c954 second-generation duplex observation; these current trials do not retain evidence proving that same stage.

## Expected behavior

For a failed E07 fixture, retain a bounded diagnostic projection for each observed attachment stage: stage, whole-transfer elapsed nanoseconds, optional HTTP status/error class, and allowlisted payload-free stream counts/EOF flags. Keep the original deadline, bytes, ownership checks, retry policy, and cleanup behavior unchanged. Never retain routes, container IDs, payloads, raw error text, or response bodies in the public result.

## Scope and validation

The handoff adds failure-only diagnostic projection plus a regression that injects an attachment timeout carrying private fields and verifies that only the allowlisted stage/counters survive. The focused offline test passes. A fresh targeted E07 run against a finalized package admitted for the exact updated source and harness is still required to identify and fix the production stall; the old 297 package cannot be paired with a changed harness by bypassing source/harness identity admission.
