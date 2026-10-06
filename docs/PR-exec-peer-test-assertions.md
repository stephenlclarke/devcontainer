# fix(tests): assert exec peer observations on the test thread

The delayed-progress peer records input, EOF and read count, and the main test thread checks those values after joining. Early mismatch exits preserve the original peer behavior. Socket errors remain captured, and all existing timeout, timing and duplex-progress assertions remain intact. Production code, runtime contracts and release scope are unchanged.

The exact-main SonarCloud failure supplies the failing static-analysis evidence. Focused exec-probe tests validate the corrected assertion placement; the final release checkpoint must also pass the original GitHub quality gate.

Container-inspect mock responses now use a separate field from exec-inspect dictionaries. The initial full exec suite exposed the collision; the corrected shared guest and exec suites must pass together. This changes only test support.
