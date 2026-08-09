# Issue 46: make copied parity fixtures visible to Docker

## Problem

The focused `C04-compose-lifecycle` Docker oracle reached `devcontainer up` and returned a valid absolute `remoteWorkspaceFolder`, but the fixture probe could not find `./probe.sh`. The initial remote-workspace correction at signed local commit `56f20056c88eb9fa2acd857ce375adf84e44d59b` generated the correct `exec` command, so the remaining failure was not Dev Containers CLI argument forwarding.

The runner had copied the fixture beneath its caller-selected evidence root. In the default invocation that root was under `/private/tmp`. Colima accepted that path as a bind source but exposed an empty directory at the container mount target. The remote workspace was therefore valid but contained no copied fixture files. This is a parity-harness defect, not evidence of a Docker Compose or container-compose lifecycle defect.

## Expected behavior

- Copy each devcontainer fixture under a Docker-visible directory inside the repository: `.build/parity-workspaces`.
- Keep engine fixtures on their existing path; they do not require a devcontainer bind mount.
- Continue to select the absolute `remoteWorkspaceFolder` returned by `devcontainer up` before executing `probe.sh`.
- Mark each copied-root owner explicitly and remove only that marker-owned root after fixture cleanup.
- Refuse cleanup for a symlink, an escaped parent, a missing or changed marker, or any unexpected filesystem state.
- Retain the raw invocation, exact fingerprint, observations, and cleanup record for a single C04 Docker oracle.

## Acceptance evidence

- Signed local `main` checkpoint `3d0b6f9fd2d34b7d46278873996056f9f30517a9` copies devcontainer fixtures under `.build/parity-workspaces`, preserves engine fixtures, and implements fail-closed marker-owned cleanup.
- `python3 Tools/parity/test_run_lane.py` passes 24 focused tests; the changed workspace-selection and cleanup paths have focused coverage. The five uncovered `run_fixture` lines are pre-existing command-failure branches, not introduced workspace lifecycle code.
- The exact same-MBP Docker oracle uses `@devcontainers/cli@0.88.0` at reference commit `f683c29f64a20109b4453e5149807e390ff65133`, Docker Engine 29.2.1/API 1.53, and a Docker Compose 5.3.1 wrapper with SHA-256 `90b2705314905295de430e2e021f490666c959accba18e0a784b32aecc04a034`.
- `C04-compose-lifecycle` passes in 4.337 seconds with `primary=app`, `primary_label=true`, `recreated=true`, `restart_signal=term`, `restarted=true`, and `shutdown=true`.
- Evidence remains at marker-protected root `/private/tmp/devcontainer-c04-mount-3d0b6f9.K4fuzR`; `.build/parity-workspaces` is absent after cleanup and no Docker object with the runner's `.build` local-folder label remains.

## Compatibility and remaining risk

This change affects only the local parity runner's copied test fixtures. It does not change Dev Containers product behavior, Docker compatibility API behavior, or container-compose lifecycle semantics. The historical candidate `C04` alias/recreate gap remains tracked in [container-compose #184](https://github.com/stephenlclarke/container-compose/issues/184); it must be rerun against this now-valid Docker reference and is not resolved by this harness fix.

## Tracking

- GitHub issue: [#46](https://github.com/stephenlclarke/devcontainer/issues/46)
- Related candidate gap: [container-compose #184](https://github.com/stephenlclarke/container-compose/issues/184)
