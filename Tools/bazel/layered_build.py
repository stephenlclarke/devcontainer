#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Run the owned source-test layers in order, retaining each exact outcome.

This is a source-unit gate. It does not certify coverage, imported binary
consumption, runtime parity, signing, or release eligibility.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import time

from check_evidence import load_policy, validate
from input_identity import source_identity, tooling_identity
from package_checks.cli_process import session_members, terminate_session

ROOT = Path(__file__).resolve().parents[2]
LAYERS = (
    ("model", ("DevContainerModelTests",)),
    ("runtime_foundation", ("DevContainerProcessTests", "DevContainerStateTests")),
    ("core", ("DevContainerCoreTests",)),
    ("adapters", ("DevContainerComposeProviderTests", "DevContainerDockerAPITests",
                  "DevContainerDockerClientTests")),
    ("host", ("DevContainerAppleRuntimeTests", "DevContainerServiceTests")),
    ("cli", ("DevContainerCLITests", "DevContainerComposeCLITests")),
)
CANCELLATION_SIGNALS = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)


class Cancelled(RuntimeError):
    """An operator cancelled this controller, not a test result."""

    def __init__(self, number: int):
        self.number = number
        super().__init__("controller received " + signal.Signals(number).name)


@contextmanager
def cancellation_handlers():
    """Keep repeated cancellation from interrupting owned cleanup and receipts."""
    previous = {number: signal.getsignal(number) for number in CANCELLATION_SIGNALS}

    def cancel(number, _frame):
        for selected in CANCELLATION_SIGNALS:
            signal.signal(selected, signal.SIG_IGN)
        raise Cancelled(number)

    try:
        for number in CANCELLATION_SIGNALS:
            signal.signal(number, cancel)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


@contextmanager
def ignore_cancellation():
    previous = {number: signal.getsignal(number) for number in CANCELLATION_SIGNALS}
    try:
        for number in CANCELLATION_SIGNALS:
            signal.signal(number, signal.SIG_IGN)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: dict) -> None:
    """Replace only this fresh run's progress record; never overwrite older runs."""
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def snapshot(root: Path) -> dict:
    result = source_identity(root)
    result["tooling"] = tooling_identity(root / "Tools/bazel", root)
    return result


def plan(root: Path, profiles: tuple[str, ...]) -> list[dict]:
    """Require the ordered layer union to match the maintained unit inventory."""
    if not profiles or len(set(profiles)) != len(profiles) or not set(profiles) <= {"stock", "enhanced"}:
        raise ValueError("select unique stock/enhanced profiles")
    labels = ["//:" + name for _, names in LAYERS for name in names]
    if len(labels) != len(set(labels)):
        raise ValueError("a source suite occurs in multiple layers")
    stages = []
    for profile in profiles:
        policy = load_policy(root / "Tools/bazel/evidence-policy.json", profile)
        if set(labels) != set(policy["tests"]):
            raise ValueError("layer union differs from the maintained source-unit inventory")
        for name, tests in LAYERS:
            stages.append({
                "name": profile + "-" + name,
                "profile": profile,
                "target": "//:layer_" + name + "_tests",
                "tests": {"//:" + test: policy["tests"]["//:" + test] for test in tests},
                "policySHA256": policy["sha256"],
                "command": [str(root / "Tools/bazel/run.sh"), "test", "--config=" + profile,
                            "//:layer_" + name + "_tests"],
            })
    return stages


def stop_child(process: subprocess.Popen) -> None:
    """Use the maintained exact-session cleanup, including changed process groups."""
    with ignore_cancellation():
        terminate_session(process)


def execute(command: list[str], root: Path, output: Path, timeout: int) -> dict:
    """Run once with durable logs and bounded cancellation; never retry a failure."""
    started = time.monotonic_ns()
    with (output / "stdout.log").open("xb") as stdout, (output / "stderr.log").open("xb") as stderr:
        process = None
        try:
            # This controller is single-threaded. Block cancellation until the
            # Popen handle is assigned; restore the original mask in the child
            # as well, so its normal cancellation signals remain usable.
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, CANCELLATION_SIGNALS)
            try:
                process = subprocess.Popen(
                    command, cwd=root, stdout=stdout, stderr=stderr, start_new_session=True,
                    preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask))
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            status = process.wait(timeout=timeout)
            if session_members(process.pid):
                raise RuntimeError("layer command exited while owned descendants remained")
        except BaseException:
            if process is not None:
                stop_child(process)
            raise
    return {"exitCode": status, "elapsedNS": time.monotonic_ns() - started}


def invocation_from_log(path: Path) -> Path:
    matches = re.findall(r"^Bazel evidence: (/[^\n]+)$", path.read_text(), re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("stage must name exactly one retained Bazel invocation")
    selected = Path(matches[0])
    if not selected.resolve().is_relative_to("/Volumes/SSD/cf/bazel"):
        raise ValueError("stage evidence is outside the enrolled Bazel store")
    return selected


def admit_command(events: list[dict], stage: dict) -> None:
    """Require the unfiltered source-debug command, not just enough test cases."""
    commands = [event["unstructuredCommandLine"]["args"] for event in events
                if "unstructuredCommandLine" in event]
    options = [event["optionsParsed"]["cmdLine"] for event in events if "optionsParsed" in event]
    if len(commands) != 1 or len(options) != 1 or not commands[0] or commands[0][0] != "test":
        raise ValueError("layer command or source mode changed")
    arguments, effective = commands[0][1:], options[0]
    profile = "--config=" + stage["profile"]
    if ([arg for arg in arguments if arg.startswith("--config=")] != [profile]
            or [arg for arg in effective if arg.startswith("--config=")] != [profile]
            or [arg for arg in arguments if not arg.startswith("-")] != [stage["target"]]
            or [arg for arg in effective if arg.startswith("--compilation_mode=")] != ["--compilation_mode=dbg"]):
        raise ValueError("layer command or source mode changed")
    forbidden = ("--test_filter", "--test_arg", "--test_lang_filters", "--test_tag_filters",
                 "--test_size_filters", "--test_timeout_filters", "--build_tag_filters",
                 "--build_tests_only", "--runs_per_test", "--override_", "--inject_repository",
                 "--repo_env=DEVCONTAINER_ARGUMENT_PARSER_LAYER", "--@", "--flagfile",
                 "--target_pattern_file")
    if (any(arg.startswith(forbidden) for arg in (*arguments, *effective))
            or any(arg.startswith(("--test_env", "--run_under", "--compilation_mode", "-c"))
                   for arg in arguments)
            or any(arg.startswith("--flaky_test_attempts") and arg != "--flaky_test_attempts=1"
                   for arg in (*arguments, *effective))):
        raise ValueError("layer command is filtered, retried, overridden or not source-debug")


def admit(directory: Path, stage: dict, source: dict) -> dict:
    """Bind successful native tests to the requested profile and unchanged source."""
    before = json.loads((directory / "inputs-before.json").read_text())
    after = json.loads((directory / "inputs-after.json").read_text())
    outcome = json.loads((directory / "outcome.json").read_text())
    if before != source or after != source:
        raise ValueError("layer invocation differs from the run's source/tooling snapshot")
    if outcome.get("bazel_exit_code") != 0 or outcome.get("validation_exit_code") != 0:
        raise ValueError("layer invocation did not pass validation")
    events = [json.loads(line) for line in (directory / "events.json").read_text().splitlines()]
    admit_command(events, stage)
    finished = [event["finished"] for event in events if "finished" in event]
    summaries = [event for event in events if "testSummary" in event]
    if (len(finished) != 1 or finished[0].get("overallSuccess") is not True
            or len(summaries) != len(stage["tests"])):
        raise ValueError("layer command, source mode or complete single-attempt result changed")
    counts = validate(events, warm=False, expected=stage["tests"])
    return {"invocation": str(directory), **counts,
            "evidenceSHA256": {name: digest(directory / name) for name in
                               ("inputs-before.json", "inputs-after.json", "events.json", "outcome.json")}}


def run(root: Path, output: Path, profiles: tuple[str, ...], timeout: int,
        development: bool = False) -> dict:
    stages = plan(root, profiles)
    if timeout < 1 or timeout > 7200:
        raise ValueError("stage timeout must be between 1 and 7200 seconds")
    if (not output.is_absolute() or output.exists() or output.is_symlink()
            or output.resolve().is_relative_to(root.resolve())):
        raise ValueError("evidence must be a fresh absolute directory outside the checkout")
    source = snapshot(root)
    if source["dirty"] and not development:
        raise ValueError("source-layer admission requires a clean checkpoint; use --development for diagnostic work")
    output.mkdir(parents=True, mode=0o700)
    write(output / "source.json", source)
    receipt = {"schema": 1, "scope": "ordered-source-unit-layers", "sourceCommit": source["commit"],
               "sourceSHA256": digest(output / "source.json"), "developmentProof": development,
               "profiles": list(profiles), "status": "running", "stages": [],
               "coverageQualified": False, "releaseQualified": False}
    write(output / "layers.json", receipt)
    with cancellation_handlers():
        try:
            for stage in stages:
                if snapshot(root) != source:
                    raise ValueError("source changed between layers")
                directory = output / stage["name"]
                directory.mkdir(mode=0o700)
                row = {**stage, "status": "running"}
                receipt["stages"].append(row)
                write(output / "layers.json", receipt)
                print("Running " + stage["name"], flush=True)
                try:
                    row.update(execute(stage["command"], root, directory, timeout))
                    if row["exitCode"]:
                        raise ValueError("layer command failed: " + stage["name"])
                    row["admission"] = admit(invocation_from_log(directory / "stderr.log"), stage, source)
                    if snapshot(root) != source:
                        raise ValueError("source changed during layer admission")
                    row["status"] = "passed"
                except BaseException:
                    row["status"] = "failed"
                    raise
                write(output / "layers.json", receipt)
            receipt["status"] = "passed"
            write(output / "layers.json", receipt)
        except BaseException as error:
            receipt["status"] = "failed"
            receipt["failure"] = type(error).__name__ + ": " + str(error)
            with ignore_cancellation():
                write(output / "layers.json", receipt)
            raise
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("stock", "enhanced", "both"), default="both")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--stage-timeout", type=int, default=3600)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--plan", action="store_true", help="print the exact sequence without running it")
    args = parser.parse_args()
    profiles = ("stock", "enhanced") if args.profile == "both" else (args.profile,)
    if args.plan:
        print(json.dumps(plan(ROOT, profiles), indent=2, sort_keys=True))
    elif args.output is None:
        parser.error("--output is required to retain an execution")
    else:
        run(ROOT, args.output, profiles, args.stage_timeout, args.development)


if __name__ == "__main__":
    main()
