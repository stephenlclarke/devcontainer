# Homebrew renderer contract

## Problem

Publication run 37413702737 verified, attested and staged the unchanged signed 1.1.0 package, then stopped before host mutation because the installation validator omitted the blank line emitted by the stable formula renderer.

## Required result and validation

The exact-template validator must accept the maintained stable renderer output while rejecting modified formula code. The focused regression must run the actual renderer rather than constructing a second formula implementation. The production archive, qualification, tag, strict restoration policy and installation transaction remain unchanged.
