# Issue: deliver enhanced dependency patches to SwiftPM source checks

## Motivation

The enhanced Bazel graph applies three reviewed patches to its pinned dependencies: zstd public headers, Containerization unaligned ext4 reads, and Engine API recovery capability routes. Hosted SwiftPM source checks resolved the same commits without those patches. In particular, Engine API `48e44d74d738ca3d24351ba02c4869be1a3e6998` lacks the capability declarations used by Devcontainer, so its unprepared checkout cannot compile the enhanced source checks.

## Required behaviour

Prepare the exact selected SwiftPM scratch tree before a compiling Make target. Authenticate each patch recipient's resolved revision, checkout HEAD, scratch-local bare mirror and upstream URL; apply only the existing checksum-pinned Bazel patch bytes. Reject staged, unrelated, partial or altered changes. A repeat preparation must accept only the exact reviewed result. Stock preparation must leave the authoritative stock selection unchanged.

## Scope and compatibility

This change delivers existing patches through the source-check path. It does not replace the native Bazel build, change dependency pins, rebuild released lower layers, or qualify final product/runtime behaviour. Signed packaging continues to consume the finalized native archive. Patch-recipient checks complement the complete source-graph and release-lock admission checks; they do not authenticate unrelated dependency rows by themselves.

## Validation and remaining gates

Focused checkout regressions and preparation against the real resolved SwiftPM mirrors are required before integration. Hosted compilation, final source quality and complete package/runtime qualification remain separate gates. See [the implementation handoff](PR-swiftpm-dependency-patches.md) and [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).

## Engine API pin refresh follow-up

After the Engine API source refresh, CodeQL run `37301392159`, Documentation run `37301392198` and Homebrew run `37301392277` stopped before compilation because source preparation still expected enhanced Engine API `48e44d74d738ca3d24351ba02c4869be1a3e6998` and stock Engine API `40436017e1e93012b8dab7cfc3c79783538065c3`. The authoritative resolved locks now select enhanced `6e8c932fc8755a4b922fd239426e9029be0554e0` and stock `36de2d66d4a1f7eb48c08d94cf1444f93d5f9c77`. Align only those two preparation expectations and the synthetic stock fixture. Add a regression that validates both actual checked-in profile locks with the maintained patch specifications, so future selection drift fails local unit preparation. Patch bytes, dependency sources, root locks and published compiled-layer recipes remain unchanged by this follow-up.
