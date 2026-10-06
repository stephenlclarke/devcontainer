# Keep exec transport assertions on the test thread

The exact-main SonarCloud analysis reports three critical S5779 findings because the delayed-progress regression invokes unittest assertions inside a peer thread whose exception handler catches assertion failures. Record peer observations and assert them after joining the thread, preserving all payload, EOF, deadline and progress requirements.

The focused shared suite also exposes a mock field collision: container-inspect override tuples and exec-inspect override dictionaries share the same name on the inherited server. Give the container override its own field so ordinary container inspection remains valid in exec tests.
