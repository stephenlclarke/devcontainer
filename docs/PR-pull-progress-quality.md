# PR: reuse bounded pull progress handling

## Summary

Pull uses the shared progress handler with an explicit quiet option. Quiet mode still consumes and validates every record while discarding output. Image-create requests assemble the reference query separately from the fixed protocol route, preserving the exact request bytes.

## Compatibility and validation

The CLI command, image references, local socket routing, deadlines and error handling remain the same. Existing pull and build regressions cover the shared handler. Focused checks and renewed exact-main release qualification are required before publication.

## Linked findings

Addresses `swift:S3087` and `swift:S1075` from SonarQube run `37274384387` on `9f7364e1bde142e8b1139daac1b22e9e67f7e379`.

Focused stock Docker client, Dev Container CLI and Compose CLI tests pass in invocation `a0531e99-4f4b-464e-b186-763abc9b91e6`. Formatting and focused static analysis pass. Full coverage and fresh exact-main release gates remain required.

SonarQube run `37279911103` on `5485f889bc5ede913241b02a6d9b68217c6d1664` also requires an explicit quiet-progress discard (`swift:S1186`) and a fixed path separated from the query delimiter (`swift:S1075`). The follow-up keeps both behaviors unchanged and adds no configurable endpoint. Focused stock Docker client and CLI tests pass in invocation `e8e893aa-c0bb-4ccb-be5c-40b927fe08fd`; formatting and focused static analysis pass. Fresh exact-main release gates and Sonar verification remain required.
