"""Locked OCI Features and specific missing-frozen-lock rejection via the CLI."""

import json
from pathlib import Path

from case_evidence import canonical
from devcontainer_build_reference import DevcontainerBuildReference
from devcontainer_reference import DevcontainerReference
from guest_runtime import diagnostic_snapshot
from host_runtime import deadline


FIXTURE = "D05-features"
IMAGE = "ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90"
FEATURES = {
    "ghcr.io/devcontainers/features/common-utils:2": {
        "installZsh": False, "installOhMyZsh": False, "upgradePackages": False, "username": "root"},
    "ghcr.io/devcontainers/features/git:1": {"version": "os-provided", "ppa": False},
}
LOCKS = (
    ("common-utils", "2", "2.5.9", "cb0c4d3c276f157eed17935747e364178d75fee17f55c4e129966f64633deb3a"),
    ("git", "1", "1.3.8", "fd75977de13a9979000e0e78baf949adb0ca71d2398995fa22e0a36d7e7e7fe2"),
)
NEGATIVE = "devcontainer-frozen-lock"


def fixture_inputs(repository: Path) -> dict:
    root = repository / "Tests/Parity/fixtures" / FIXTURE
    result = {key: (root / path).read_text() for key, path in (
        ("configuration", ".devcontainer/devcontainer.json"), ("probe", "probe.sh"),
        ("lockfile", ".devcontainer/devcontainer-lock.json"))}
    config = json.loads(result["configuration"])
    expected = {"features": {
        f"ghcr.io/devcontainers/features/{name}:{major}": {
            "version": version, "resolved": f"ghcr.io/devcontainers/features/{name}@sha256:{sha}",
            "integrity": "sha256:" + sha} for name, major, version, sha in LOCKS}}
    if (config.get("image") != IMAGE or config.get("features") != FEATURES or
            config.get("remoteUser") != "root" or config.get("overrideCommand") is not True or
            json.loads(result["lockfile"]) != expected):
        raise ValueError("D05 configuration or Feature lock pin changed")
    return result


class DevcontainerFeaturesReference(DevcontainerBuildReference):
    fixture = FIXTURE
    reference_image = IMAGE
    keys = {"feature_git", "feature_jq", "lockfile"}
    up_timeout = 900
    negative_stderr_suffix = ".stderr.log"
    commands = (*DevcontainerReference.commands, NEGATIVE)

    def write_workspace(self):
        DevcontainerReference.write_workspace(self)
        self.lock_path.write_text(self.inputs["devcontainerFixture"]["lockfile"])

    @property
    def lock_path(self):
        return self.workspace / ".devcontainer/devcontainer-lock.json"

    def expected_metadata(self):
        # Projection of the two digest-authenticated Feature manifests through
        # the pinned CLI's Tt/xV metadata fields, in their install order.
        return [{"id": "ghcr.io/devcontainers/features/common-utils:2"},
                {"id": "ghcr.io/devcontainers/features/git:1", "customizations": {"vscode": {"settings": {
                    "github.copilot.chat.codeGeneration.instructions": [{"text":
                        "This dev container includes an up-to-date version of Git, built from source as needed, "
                        "pre-installed and available on the `PATH`."}]}}}},
                {"remoteUser": "root", "overrideCommand": True}]

    def arguments(self, command):
        arguments = super().arguments(command)
        return arguments + (["--frozen-lockfile"] if command == "up" else [])

    def operation(self):
        with deadline(self.up_timeout + 180):
            return self.execute()

    def execute(self):
        if self.journal.records().get("devcontainer-plan.json") != canonical(self.plan):
            raise ValueError("D05 plan identity changed")
        self.missing_lock()
        result = super().execute()
        if self.lock_path.read_text() != self.inputs["devcontainerFixture"]["lockfile"]:
            raise ValueError("D05 frozen positive run modified the checked-in lockfile")
        return {**result, "frozen_lock": "true"}

    def missing_lock(self):
        # A fresh workspace/owner has no container to remove. Unlike the legacy
        # test, rejection cannot destroy the positive case or adopt an old one.
        if self.find() is not None:
            raise ValueError("D05 negative test requires an empty owned inventory")
        if self.lock_path.read_text() != self.inputs["devcontainerFixture"]["lockfile"]:
            raise ValueError("D05 input lockfile changed")
        backup = self.lock_path.with_suffix(".json.parity-missing")
        if backup.exists():
            raise ValueError("D05 missing-lock test was already attempted")
        self.lock_path.rename(backup)
        try:
            try:
                self.vm.command(NEGATIVE, self.arguments("up") + ["--user-data-folder",
                                str(self.vm.root / "negative-data")], timeout=120, separate_output=True)
            except RuntimeError:
                self.verify_rejection()
            else:
                raise ValueError("D05 missing frozen lockfile was accepted")
            if self.find() is not None or self.lock_path.exists():
                raise ValueError("D05 rejected request created a container or lockfile")
            self.journal.put("devcontainer-frozen-rejected.json", canonical({"missingLockRejected": True}))
        finally:
            if self.lock_path.exists():
                raise ValueError("D05 unexpected lockfile retained beside original backup")
            backup.rename(self.lock_path)

    def verify_rejection(self):
        records = self.journal.records()
        exited = json.loads(records.get(NEGATIVE + "-exit.json", b"null"))
        output, meta = diagnostic_snapshot(self.vm.root / (NEGATIVE + ".log"))
        errors, error_meta = diagnostic_snapshot(self.vm.root / (NEGATIVE + self.negative_stderr_suffix))
        result = json.loads(output)
        logs = [json.loads(line) for line in errors.splitlines() if line.strip()]
        diagnostic = any(isinstance(event, dict) and isinstance(event.get("text"), str) and
                         "Error: Lockfile does not exist." in event["text"].splitlines() for event in logs)
        if (self.vm.uncertain or not isinstance(exited, dict) or type(exited.get("code")) is not int or
                exited["code"] != 1 or json.loads(meta)["truncated"] or json.loads(error_meta)["truncated"] or
                not isinstance(result, dict) or result.get("outcome") != "error" or
                result.get("containerId") is not None or not diagnostic):
            raise ValueError("D05 did not reject the missing frozen lockfile for the expected reason")
