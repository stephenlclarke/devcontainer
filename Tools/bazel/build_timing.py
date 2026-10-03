"""Measure one Bazel process and compare sealed observations; never schedule work."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import time


def command_text(arguments: list[str]) -> str:
    """Read non-secret platform metadata without depending on caller PATH."""
    return subprocess.check_output(arguments, text=True, stderr=subprocess.DEVNULL).strip()


def host_identity() -> dict:
    """Identify the comparison platform without retaining its network name."""
    return {
        "machine": hashlib.sha256(platform.node().encode()).hexdigest(),
        "architecture": platform.machine(),
        "os": platform.mac_ver()[0],
        "model": command_text(["/usr/sbin/sysctl", "-n", "hw.model"]),
        "logicalCPUs": os.cpu_count(),
        "xcode": command_text(["/usr/bin/xcodebuild", "-version"]),
        "swift": command_text(["/usr/bin/xcrun", "swift", "--version"]),
    }


def measure(output: Path, profile: str, arguments: list[str]) -> int:
    """Time the child only, excluding metadata collection and evidence retention."""
    host = host_identity()
    before = os.getloadavg()
    started = time.time_ns()
    monotonic = time.monotonic_ns()
    # Apple's /usr/bin/python3 shim can inject SDKROOT/CPATH/LIBRARY_PATH even
    # when its caller uses env -i. Do not pass that undeclared toolchain state
    # into Bazel or widen the evidence environment allowlist to accept it.
    names = {"HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP",
             "DEVELOPER_DIR", "PYTHONDONTWRITEBYTECODE", "DEVCONTAINER_HOST_INTEGRATION"}
    environment = {name: value for name, value in os.environ.items() if name in names}
    status = subprocess.run(arguments, check=False, env=environment).returncode
    elapsed = time.monotonic_ns() - monotonic
    record = {
        "schema": 1, "runtimeProfile": profile, "host": host,
        "configurationSHA256": hashlib.sha256(json.dumps([
            # Source SHA is retained independently in the invocation snapshot.
            # Keep stamped vs unstamped configurations distinct, but permit
            # like-for-like release-build comparisons across source revisions.
            "--define=DEVCONTAINER_COMMIT=<source>" if argument.startswith("--define=DEVCONTAINER_COMMIT=") else argument
            for argument in arguments[1:] if not argument.startswith("--build_event_json_file=")
        ]).encode() + Path(".bazelrc").read_bytes()).hexdigest(),
        "startedUnixNS": started, "elapsedNS": elapsed, "exitCode": status,
        "loadBefore": before, "loadAfter": os.getloadavg(),
        # Low load is not proof of a controlled quiet machine. Ordinary build
        # observations must not silently become authoritative benchmarks.
        "quietHostVerified": False, "kind": "build-observation",
    }
    with output.open("x") as stream:
        json.dump(record, stream, sort_keys=True)
        stream.write("\n")
    return status if status >= 0 else 128 - status


def observation(database: Path, invocation: str) -> dict:
    """Read authenticated timing/provenance only, never historic raw events."""
    from retain_evidence import digest
    from input_identity import verify

    if database.is_symlink() or not database.is_file():
        raise ValueError("Missing or symlinked timing database")
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as db:
        row = db.execute("SELECT manifest, exit_code FROM invocations WHERE id=?", (invocation,)).fetchone()
        if row is None:
            raise ValueError("Unknown invocation")
        manifest = json.loads(row[0])

        def read(name: str) -> dict:
            expected = manifest.get(name)
            blob = db.execute("SELECT bytes FROM blobs WHERE sha256=?", (expected,)).fetchone()
            if blob is None or digest(blob[0]) != expected:
                raise ValueError("Missing or corrupt timing evidence; old runs are not invented")
            return json.loads(blob[0])

        timing = read("timing.json")
        inputs = read("inputs-before.json")
        after = read("inputs-after.json")
        verify(inputs, after)
        summary = read("build-metrics.json")
    return dict(timing, invocation=invocation, sourceCommit=inputs["commit"],
                dirty=inputs["dirty"] or after["dirty"], validationExitCode=row[1], metrics=summary)


def compare(baseline: dict, candidate: dict) -> dict:
    """Ratios are informational, and incompatible or failed runs are not compared."""
    mismatches = [key for key in ("host", "runtimeProfile", "configurationSHA256") if baseline[key] != candidate[key]]
    for key in ("command", "targets", "bazelVersion"):
        if baseline["metrics"].get(key) != candidate["metrics"].get(key):
            mismatches.append(key)
    if any(item["exitCode"] or item["validationExitCode"] or item["dirty"] for item in (baseline, candidate)):
        mismatches.append("failed-or-dirty-input")
    durations = [item["elapsedNS"] for item in (baseline, candidate)]
    if any(not isinstance(value, int) or value <= 0 for value in durations):
        raise ValueError("Invalid measured duration")
    return {
        "baseline": baseline, "candidate": candidate, "incompatible": mismatches,
        "candidateOverBaseline": None if mismatches else durations[1] / durations[0],
        "authoritativeBenchmark": False,
        "note": "Cache state and machine interference require a controlled campaign; these are raw observations.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    timing = sub.add_parser("measure")
    timing.add_argument("output", type=Path)
    timing.add_argument("profile", choices=("stock", "enhanced"))
    timing.add_argument("command", nargs=argparse.REMAINDER)
    report = sub.add_parser("report")
    report.add_argument("invocation")
    report.add_argument("--baseline")
    args = parser.parse_args()
    if args.mode == "measure":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            parser.error("A single command is required")
        raise SystemExit(measure(args.output, args.profile, command))
    root = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if root.resolve() != root or root.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("Timing evidence must remain on internal storage")
    database = root / "bazel-evidence.sqlite"
    result = observation(database, args.invocation)
    if args.baseline:
        result = compare(observation(database, args.baseline), result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
