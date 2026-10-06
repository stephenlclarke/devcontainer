# Homebrew bootout completion

## Problem

Publication run 37415310954 failed after launchd acknowledged stopping the captured Homebrew job, because the installation helper required its registration to disappear immediately. The transaction restored the original baseline and retained a failed-restored receipt; no formula installation was admitted.

## Required result

Use the existing bounded 15-second quiescence wait for both the exact captured registration and its processes. Reject a changed registration immediately. Do not retry installation, increase the deadline, kill foreign processes or change package/runtime qualification.
