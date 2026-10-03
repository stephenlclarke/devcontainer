#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Build all four executables using released layers and retain consumption proof."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from artifacts.prove_layers import prove
from layered_build import (ROOT, cancellation_handlers, digest, execute,
                           ignore_cancellation, invocation_from_log, snapshot, write)


def run(root: Path, output: Path, profiles: tuple[str, ...], timeout: int,
        development: bool = False) -> dict:
    if not profiles or len(set(profiles)) != len(profiles) or not set(profiles) <= {"stock", "enhanced"}:
        raise ValueError("select unique stock/enhanced profiles")
    if not 1 <= timeout <= 7200:
        raise ValueError("stage timeout must be between 1 and 7200 seconds")
    if (not output.is_absolute() or output.exists() or output.is_symlink()
            or output.resolve().is_relative_to(root.resolve())):
        raise ValueError("evidence must be a fresh absolute directory outside the checkout")
    source = snapshot(root)
    if source["dirty"] and not development:
        raise ValueError("compiled consumption requires a clean checkpoint; use --development for diagnostics")
    output.mkdir(parents=True, mode=0o700)
    write(output / "source.json", source)
    result = {"schema": 1, "scope": "released-layer-consumption", "status": "running",
              "sourceCommit": source["commit"], "sourceSHA256": digest(output / "source.json"),
              "developmentProof": source["dirty"], "releaseQualified": False, "profiles": {}}
    write(output / "consumers.json", result)
    with cancellation_handlers():
        try:
            for profile in profiles:
                row = {"status": "running", "commands": []}
                result["profiles"][profile] = row
                flags = ["--config=" + profile, "--config=release", "--config=prebuilt-container-sdk"]
                selected = {}
                commands = (("build", ["//:products"]),
                            ("aquery", ["deps(//:products)", "--output=jsonproto"]),
                            ("info", ["output_base"]))
                for operation, arguments in commands:
                    if snapshot(root) != source:
                        raise ValueError("source changed between compiled consumer commands")
                    directory = output / (profile + "-" + operation)
                    directory.mkdir(mode=0o700)
                    command = [str(root / "Tools/bazel/run.sh"), operation, *flags, *arguments]
                    item = {"operation": operation, "command": command, "status": "running"}
                    row["commands"].append(item)
                    write(output / "consumers.json", result)
                    print("Running " + profile + " compiled consumer " + operation, flush=True)
                    item.update(execute(command, root, directory, timeout))
                    if item["exitCode"]:
                        raise ValueError(profile + " compiled consumer " + operation + " failed")
                    if snapshot(root) != source:
                        raise ValueError("source changed during compiled consumer command")
                    if operation != "info":
                        invocation = invocation_from_log(directory / "stderr.log")
                        if json.loads((invocation / "inputs-before.json").read_text()) != source:
                            raise ValueError("consumer invocation differs from controller source")
                        item["invocation"] = str(invocation)
                    item["stdoutSHA256"] = digest(directory / "stdout.log")
                    item["stderrSHA256"] = digest(directory / "stderr.log")
                    item["status"] = "passed"
                    selected[operation] = directory
                    write(output / "consumers.json", result)
                output_base = Path((selected["info"] / "stdout.log").read_text().strip())
                if not output_base.is_absolute() or not output_base.resolve().is_relative_to("/Volumes/SSD/cf/bazel/output"):
                    raise ValueError("consumer output base is outside enrolled Bazel storage")
                proof_path = output / (profile + "-compiled-consumer.json")
                query_invocation = invocation_from_log(selected["aquery"] / "stderr.log")
                # The outer stdout also includes the launcher's retention
                # status. Prove the native query stream captured under its lease.
                raw_query = query_invocation / "aquery.stdout.log"
                row["actionGraphSHA256"] = digest(raw_query)
                prove(invocation_from_log(selected["build"] / "stderr.log"),
                      query_invocation, raw_query, output_base,
                      output_base / "execroot/_main", profile, proof_path, root)
                if snapshot(root) != source:
                    raise ValueError("source changed during compiled consumer admission")
                row.update(status="passed", proof=str(proof_path), proofSHA256=digest(proof_path))
                write(output / "consumers.json", result)
            result["status"] = "passed"
            write(output / "consumers.json", result)
        except BaseException as error:
            result["status"] = "failed"
            result["failure"] = type(error).__name__ + ": " + str(error)
            for row in result["profiles"].values():
                if row["status"] == "running":
                    row["status"] = "failed"
                for item in row["commands"]:
                    if item["status"] == "running":
                        item["status"] = "failed"
            with ignore_cancellation():
                write(output / "consumers.json", result)
            raise
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("stock", "enhanced", "both"), default="both")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stage-timeout", type=int, default=3600)
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    profiles = ("stock", "enhanced") if args.profile == "both" else (args.profile,)
    run(ROOT, args.output, profiles, args.stage_timeout, args.development)


if __name__ == "__main__":
    main()
