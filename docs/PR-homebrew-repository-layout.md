# Admit Homebrew repository layouts

## Result

Installation preflight records Homebrew's actual repository and checks that it is a physical user-owned directory within the admitted prefix. The temporary tap must reside under that repository's fixed Library/Taps/stephenlclarke directory with its exact CI name. Apple Silicon and nested Intel layouts are supported without allowing a foreign repository.

## Validation and compatibility

Focused tests exercise both repository layouts and reject a repository outside the prefix before replacement. The existing installation, process quiescence and baseline restoration tests remain required. No immutable release asset, package qualification or signed tag changes; real installation/restoration remains mandatory before GA promotion.
