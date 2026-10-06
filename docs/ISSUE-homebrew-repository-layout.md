# Homebrew repository layout

## Problem

Publication run 37415613138 successfully quiesced the captured service, then rejected the temporary tap because it assumed the Homebrew repository lived under a Homebrew subdirectory of the prefix. On this Apple Silicon host Homebrew reports /opt/homebrew as its repository. The transaction restored the baseline and retained a failed-restored receipt.

## Required result

Admit the actual canonical, user-owned repository reported by Homebrew before host mutation. Derive the permitted temporary tap parent from that repository while preserving exact namespace, ownership, canonical path, formula and restoration checks.
