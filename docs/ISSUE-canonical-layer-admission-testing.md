# Issue: run legacy layer admission with its canonical Python interpreter

## Motivation

The foundation layer verifier fingerprints a narrowly normalized Python AST to admit exact existing stock archives without rebuilding their dependencies. The production importer and verification paths use `/usr/bin/python3`, while the generic lint command inherited Homebrew Python 3.14 from `PATH`. Python 3.13 and later default `ast.dump` to omit empty fields, so the verifier correctly fails closed under those interpreters and the test suite incorrectly treated them as compatible.

## Required behaviour

Run the foundation admission suite with the same canonical `/usr/bin/python3` used by the maintained native import and verification tools. Exercise available supported AST serializers and explicitly assert that newer serializers which omit empty fields reject legacy admission. Keep ordinary recipe, source, lock, and archive checks strict.

## Scope and compatibility

This is a test-runner and documentation correction. It must not change `foundation.py`, package recipes, locks, or any released producer/archive identity. Newer Python interpreters remain fail-closed until a separately reviewed verifier transition can preserve the historical producer contract.

## Validation and remaining gates

The complete foundation test module must pass with `/usr/bin/python3`; its focused interpreter matrix must confirm admission under Python 3.9 and 3.12 and rejection under Python 3.13/3.14 when those interpreters are available. Other lint suites continue to use the selected project Python. Release qualification and source-quality gates remain independent.

Linked implementation: [canonical layer admission tests](PR-canonical-layer-admission-testing.md). Integration review: [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).
