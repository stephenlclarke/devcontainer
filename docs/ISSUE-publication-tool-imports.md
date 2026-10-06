# Publication tool imports

## Problem

Publication run 37414990215 authenticated and reused all immutable 1.1.0 assets, but installation stopped before host mutation because the pinned sparse tool checkout omitted Tools/bazel. The installation service adapter transitively imports guest runtime helpers from that directory.

## Required result and validation

Check out the complete maintained Tools directory at the pinned workflow commit. Construct the real installation service adapter without invoking any host operations before package publication begins. Validate that construction in an isolated checkout with the actual sparse selection. The immutable package, source tag, qualification and installation/restoration admission remain unchanged.
