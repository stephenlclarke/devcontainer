# Wait for Homebrew bootout completion

## Result

The installation helper waits for asynchronous removal of the captured launchd registration within the existing process-stop deadline. A registration with a different identity fails immediately, and surviving captured processes still prevent replacement. Baseline restoration and guard ownership remain unchanged.

## Validation and compatibility

Focused regressions model delayed registration removal and reject a substituted registration. Existing survivor and reversible installation tests remain required. The immutable 1.1.0 archive and tag remain unchanged; publication resumes with separately recorded tool identity and still requires a real passed-restored receipt before tap promotion and GA.
