# Issue: positive socket fixtures inherit an oversized temporary path

## Problem

After introducing the Darwin socket-path admission guard, the default macOS temporary parent makes three existing mocked lifecycle fixtures fail before their intended boundary. A short caller-supplied `TMPDIR` concealed this fixture problem in focused validation.

## Expected behavior

Positive socket fixtures must allocate under a short private parent on the enrolled SSD on this Mac. Oversized-path regressions must retain their real rejection checks. No production admission check should be mocked or weakened.

## Related work

See [the correction handoff](PR-native-socket-fixture-scratch.md) and [the socket-path issue](ISSUE-native-provider-socket-path.md).
