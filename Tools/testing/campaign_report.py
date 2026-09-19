"""Read sealed runtime evidence and render public, explicitly scoped comparisons."""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import xml.etree.ElementTree as ET

from case_evidence import CaseStore, LANES, canonical, compare_cases, contract_observations, digest, validate_identity


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def contracts(repository: Path, selected: list[str]) -> tuple[dict, list[str]]:
    """Read the public contracts; never infer a smaller scope from present results."""
    manifest = json.loads((repository / "Tests/Parity/manifest.json").read_bytes())
    names = [item["id"] for item in manifest["fixtures"]]
    if (not names or len(set(names)) != len(names) or
            any(not isinstance(name, str) or not IDENTIFIER.fullmatch(name) for name in names)):
        raise ValueError("Invalid fixture inventory")
    requested = selected or names
    if len(set(requested)) != len(requested) or not set(requested).issubset(names):
        raise ValueError("Unknown or duplicate selected fixture")
    expected = {}
    for name in requested:
        raw = json.loads((repository / f"Tests/Parity/fixtures/{name}/contract.json").read_bytes())["expected"]
        expected[name] = contract_observations(raw)
    return expected, names


def read_records(database: Path, campaign: str, fixtures: set[str]) -> list[dict]:
    """Take a read-only consistent snapshot, authenticating results and private artifacts."""
    if not IDENTIFIER.fullmatch(campaign):
        raise ValueError("Invalid campaign")
    info = database.lstat()
    if (database.resolve() != database or not stat.S_ISREG(info.st_mode) or
            info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077):
        raise ValueError("Evidence must be a private canonical regular file")
    records = []
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("BEGIN")
        for key, identity_bytes, result_bytes, checksum in db.execute("SELECT id,identity,result,sha256 FROM cases"):
            identity = json.loads(identity_bytes)
            if identity.get("campaign") != campaign or identity.get("fixture") not in fixtures:
                continue
            if validate_identity(identity) != key:
                raise ValueError("Stored case key differs from identity")
            result = CaseStore.read_row(identity, (identity_bytes, result_bytes, checksum), db)
            records.append({"identity": identity, "result": result, "sealSHA256": checksum})
    return records


def public_case(record: dict, expected: dict) -> dict:
    """Never publish raw logs, paths, errors or unexpected observation payloads."""
    result = record["result"]
    return {"identity": record["identity"], "sealSHA256": record["sealSHA256"],
            "status": result["status"], "durationsNS": result["durationsNS"],
            "observationsMatch": result["observations"] == expected,
            "observationsSHA256": digest(canonical(result["observations"])),
            "errorCount": len(result["errors"]), "cleanupStatus": result["cleanup"]["status"],
            "remainingOwnedResourceCount": len(result["cleanup"]["remainingOwnedResources"])}


def report(database: Path, repository: Path, campaign: str, selected: list[str]) -> dict:
    expected, inventory = contracts(repository, selected)
    records = read_records(database, campaign, set(expected))
    shared = {(record["identity"]["harnessSHA256"], record["identity"]["releaseSetSHA256"]) for record in records}
    if len(shared) > 1:
        raise ValueError("Cannot mix harness or release identities within a campaign")
    fixtures = []
    for name, contract in expected.items():
        cases = [record for record in records if record["identity"]["fixture"] == name]
        lanes = [record["identity"]["lane"] for record in cases]
        if len(set(lanes)) != len(lanes):
            raise ValueError("Ambiguous campaign: multiple identities for one fixture/lane")
        for record in cases:
            if record["identity"]["contractSHA256"] != digest(canonical(contract)):
                raise ValueError("Stored case contract differs from the current public contract")
        missing = sorted(set(LANES) - set(lanes))
        comparison = compare_cases([{key: record[key] for key in ("identity", "result")} for record in cases], contract) if not missing else {
            "functionalParity": False, "differences": ["Missing lane: " + lane for lane in missing],
            "operationRatios": {}, "timingQualified": False,
            "timingNote": "Incomplete evidence; no three-lane timing comparison."}
        fixtures.append({"fixture": name, "expected": contract, "missingLanes": missing,
                         "cases": [public_case(record, contract) for record in sorted(cases, key=lambda item: item["identity"]["lane"])],
                         "comparison": comparison,
                         "rawOrderOfMagnitudeCandidates": sorted(lane for lane, ratio in comparison["operationRatios"].items()
                                                                  if lane != "docker" and ratio is not None and ratio >= 10)})
    return {"schema": 1, "campaign": campaign, "scope": "explicit-fixture-functional-comparison",
            "requestedFixtures": list(expected), "unrequestedFixtures": sorted(set(inventory) - set(expected)),
            "completeManifest": set(expected) == set(inventory),
            "functionalParity": all(item["comparison"]["functionalParity"] for item in fixtures),
            "timingQualified": False, "releaseQualified": False, "fixtures": fixtures}


def markdown(data: dict) -> str:
    lines = ["# Runtime campaign evidence", "", f"Campaign: `{data['campaign']}`.", "",
             "Functional result applies only to the explicitly requested fixtures below. "
             "This is not full-release qualification or a quiet paired benchmark. "
             "Raw ratios of 10x or more require investigation; they are not waived or certified as performance passes.", "",
             "| Fixture | Lane | Status | Setup s | Operation s | Cleanup s | Operation / Docker | Cleanup |",
             "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |"]
    for fixture in data["fixtures"]:
        for case in fixture["cases"]:
            lane = case["identity"]["lane"]
            durations = case["durationsNS"]
            ratio = fixture["comparison"]["operationRatios"].get(lane)
            values = " | ".join(f"{durations[phase] / 1e9:.9f}" for phase in ("setup", "operation", "cleanup"))
            ratio_text = "not compared" if ratio is None else f"{ratio:.3f}x"
            lines.append(f"| {fixture['fixture']} | {lane} | {case['status']} | {values} | {ratio_text} | {case['cleanupStatus']} |")
        for lane in fixture["missingLanes"]:
            lines.append(f"| {fixture['fixture']} | {lane} | missing | - | - | - | - | unknown |")
    lines += ["", "Functional parity: " + str(data["functionalParity"]).lower() + ".", "",
              "Unrequested fixtures: " + (", ".join(data["unrequestedFixtures"]) or "none") + ".", "",
              "The accompanying JSON binds each case to its exact harness, contract, release set, runtime and retained evidence seal.", ""]
    return "\n".join(lines)


def junit(data: dict) -> str:
    suite = ET.Element("testsuite", name="runtime-campaign")
    for fixture in data["fixtures"]:
        for lane in LANES:
            case = next((case for case in fixture["cases"] if case["identity"]["lane"] == lane), None)
            test = ET.SubElement(suite, "testcase", name=fixture["fixture"], classname=lane,
                                 time=str(sum(case["durationsNS"].values()) / 1e9 if case else 0))
            properties = ET.SubElement(test, "properties")
            values = {"scope": data["scope"], "timingQualified": "false", "releaseQualified": "false"}
            if case:
                values.update(case["identity"])
                values.update({phase + "NS": duration for phase, duration in case["durationsNS"].items()})
                values["sealSHA256"] = case["sealSHA256"]
            for key, value in values.items():
                ET.SubElement(properties, "property", name=key, value=str(value))
            if not case or case["status"] != "passed" or not case["observationsMatch"]:
                ET.SubElement(test, "failure", message="Missing or failed exact-contract evidence")
    suite.set("tests", str(len(suite)))
    suite.set("failures", str(len(suite.findall("testcase/failure"))))
    suite.set("errors", "0")
    suite.set("skipped", "0")
    return ET.tostring(suite, encoding="unicode")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign")
    parser.add_argument("--fixture", action="append", default=[])
    parser.add_argument("--format", choices=("json", "markdown", "junit"), default="json")
    args = parser.parse_args()
    retained = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if retained.resolve() != retained or retained.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("Evidence must remain on internal home storage")
    data = report(retained / "runtime-cases.sqlite", Path(__file__).resolve().parents[2], args.campaign, args.fixture)
    print({"json": lambda: json.dumps(data, indent=2, sort_keys=True),
           "markdown": lambda: markdown(data), "junit": lambda: junit(data)}[args.format]())
    raise SystemExit(0 if data["functionalParity"] else 1)


if __name__ == "__main__":
    main()
