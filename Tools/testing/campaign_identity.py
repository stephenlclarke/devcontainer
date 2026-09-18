"""One comparison identity for all published lanes; runtime identity stays lane-specific."""

from pathlib import Path

from case_evidence import canonical, digest


RELEASE_INPUTS = (
    "Tools/bazel/releases.lock.json",
    "Tools/bazel/docker-oracle.lock.json",
    "Tools/bazel/docker-cli.lock.json",
    "Tools/bazel/guest-kernel.lock.json",
    "Tools/bazel/guest-images.lock.json",
    "Tools/bazel/builder-images.lock.json",
    "Tests/Parity/manifest.json",
)
PREPARATION_HELPERS = (
    "prepare_releases.py", "prepare_candidate.py", "retain_evidence.py", "release_inputs.py",
    "prepare_guest_images.py", "oci_image_layout.py", "prepare_docker_cli.py",
)
RUNTIME_HELPERS = (
    "build_probe.py", "build_images.py", "build_fixture.py", "build_runtime.py",
    "archive_probe.py", "campaign_identity.py", "case_evidence.py", "docker_vm.py",
    "engine_probe.py", "exec_probe.py", "fault_probe.py", "guest_fixture.py", "guest_runtime.py",
    "host_runtime.py", "lifecycle_probe.py", "network_volume_probe.py",
    "private_keychain.py", "released_docker.py", "released_engine.py",
    "runtime_services.py", "runtime_probe.py", "service_journal.py", "service_switch.py",
)


def published_fingerprints(repository: Path) -> dict[str, str]:
    """Bind the entire reviewed release closure without acquiring other lanes.

    Both Bazel runtime targets use the same runfiles group. Never include a
    per-lane selected subset here: that belongs in runtimeSHA256 and would make
    authentic records impossible to compare. Historical fingerprints are not
    rewritten or upgraded by this helper.
    """
    # Python resolves imported modules through runfile symlinks, whereas the
    # entry script can retain its runfiles path. Hash the explicit execution
    # closure, not whichever tests/recovery helpers happen to share that folder.
    sources = [*[repository / "Tools/testing" / name for name in RUNTIME_HELPERS],
               *[repository / "Tools/bazel" / name for name in PREPARATION_HELPERS]]
    harness = {path.relative_to(repository).as_posix(): digest(path.read_bytes()) for path in sources}
    releases = {name: digest((repository / name).read_bytes()) for name in RELEASE_INPUTS}
    return {"harnessSHA256": digest(canonical(harness)), "releaseSetSHA256": digest(canonical(releases))}
