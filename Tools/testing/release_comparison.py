"""Compare sealed previous/current native results without certifying quiet timing."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import re
import statistics

from campaign_report import contracts, public_case, read_records
from case_evidence import canonical, digest


CANDIDATE = "local-candidate-integration-only"
PUBLISHED = {"released-engine-case-only", "released-devcontainer-case-only"}


def product_identity(record: dict) -> dict:
    """Publish only exact product identity, never executable paths or raw logs."""
    admission = record["admission"]
    product = admission["runtime"]["releases"][0]
    checksum = product["assetSHA256"]
    if not isinstance(checksum, str) or re.fullmatch(r"[a-f0-9]{64}", checksum) is None:
        raise ValueError("Invalid product asset digest")
    if admission["scope"] == CANDIDATE and product.get("scope") == CANDIDATE:
        commit = product.get("sourceCommit", "")
        if not isinstance(commit, str) or re.fullmatch(r"[a-f0-9]{40}", commit) is None:
            raise ValueError("Invalid candidate source commit")
        return {"kind": "local-candidate", "sourceCommit": commit, "assetSHA256": checksum}
    if admission["scope"] not in PUBLISHED or product.get("scope") is not None:
        raise ValueError("Unreviewed product admission scope")
    matches = [asset for asset in admission["releaseLock"]["assets"]
               if asset.get("repository") == "stephenlclarke/devcontainer" and asset.get("sha256") == checksum]
    if len(matches) != 1:
        raise ValueError("Published product differs from the retained release lock")
    asset = matches[0]
    if (asset.get("prerelease") is not False or
            re.fullmatch(r"[a-f0-9]{40}", asset.get("commit", "")) is None or
            re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", asset.get("tag", "")) is None):
        raise ValueError("Invalid stable release identity")
    return {"kind": "published-release", "sourceCommit": asset["commit"], "assetSHA256": checksum,
            "version": asset["tag"], "url": "https://github.com/stephenlclarke/devcontainer/releases/tag/" + asset["tag"]}


def controlled_runtime(record: dict) -> str:
    """Product/front-end bytes may change; provider, host and guest inputs may not.

    This admits the documented external 1.0.1 front end versus the newer bundled
    front end. They remain bound by the original runtime and evidence seals.
    Native Compose is intentionally outside this adapter's reviewed scope.
    """
    runtime = copy.deepcopy(record["admission"]["runtime"])
    runtime["releases"] = runtime["releases"][1:]
    runtime["versions"] = runtime["versions"][1:]
    guest = runtime.get("guestInputs", {})
    if "composeCandidate" in guest:
        raise ValueError("Native Compose comparison requires its own published runtime adapter")
    for key in ("devcontainerCandidate", "legacyFrontend"):
        guest.pop(key, None)
    return digest(canonical(runtime))


def summary(values: list[int]) -> dict:
    """Use Compose's existing median and nearest-rank P95 definitions, in ns."""
    return {"medianNS": statistics.median(values), "p95NS": sorted(values)[math.ceil(len(values) * .95) - 1]}


def compare(database: Path, repository: Path, baseline: list[str], target: list[str],
            fixtures: list[str], lane: str) -> dict:
    """No retries, omitted failed samples, inferred scope or quiet-host override."""
    campaigns = baseline + target
    if not baseline or len(baseline) != len(target) or len(set(campaigns)) != len(campaigns):
        raise ValueError("Equal nonempty, distinct baseline/target campaign lists are required")
    if lane not in {"apple-stock", "container-compose"} or not fixtures:
        raise ValueError("Explicit native lane and fixtures are required")
    expected, inventory = contracts(repository, fixtures)
    records = {campaign: read_records(database, campaign, set(expected), include_admission=True, lane=lane)
               for campaign in campaigns}
    rows = []
    for fixture, contract in expected.items():
        selected = {}
        for campaign in campaigns:
            matches = [record for record in records[campaign]
                       if record["identity"]["fixture"] == fixture and record["identity"]["lane"] == lane]
            if len(matches) != 1:
                raise ValueError("Missing or ambiguous comparison sample: " + campaign + "/" + fixture)
            record = matches[0]
            if record["identity"]["contractSHA256"] != digest(canonical(contract)):
                raise ValueError("Comparison contract differs from retained evidence")
            selected[campaign] = record
        if len({record["identity"]["harnessSHA256"] for record in selected.values()}) != 1:
            raise ValueError("Comparison harness differs between samples")
        if len({controlled_runtime(record) for record in selected.values()}) != 1:
            raise ValueError("Comparison host/provider/guest inputs differ")
        roles = {}
        for role, names in (("baseline", baseline), ("target", target)):
            samples = [selected[name] for name in names]
            identities = {canonical(product_identity(record)) for record in samples}
            fingerprints = {(record["identity"]["releaseSetSHA256"], record["identity"]["runtimeSHA256"])
                            for record in samples}
            if len(identities) != 1 or len(fingerprints) != 1:
                raise ValueError("Product/runtime identity changed within one comparison role")
            public = [public_case(record, contract) for record in samples]
            eligible = all(case["status"] == "passed" and case["observationsMatch"] and
                           case["durationsNS"]["operation"] > 0 for case in public)
            roles[role] = {"product": json.loads(next(iter(identities))), "samples": public,
                           "eligible": eligible, "summary": summary([case["durationsNS"]["operation"]
                                                                      for case in public]) if eligible else None}
        if roles["baseline"]["product"]["assetSHA256"] == roles["target"]["product"]["assetSHA256"]:
            raise ValueError("Previous/current comparison requires different product artifacts")
        delta = None
        if roles["baseline"]["eligible"] and roles["target"]["eligible"]:
            delta = {}
            for metric in ("medianNS", "p95NS"):
                before, after = (roles[role]["summary"][metric] for role in ("baseline", "target"))
                delta[metric] = {"savedNS": before - after, "savedPercent": (1 - after / before) * 100,
                                 "targetOverBaseline": after / before}
        rows.append({"fixture": fixture, **roles, "rawDifference": delta,
                     "rawOrderOfMagnitudeRegression": delta is not None and any(
                         value["targetOverBaseline"] >= 10 for value in delta.values())})
    return {"schema": 1, "scope": "previous-current-native-observations", "lane": lane,
            "requestedFixtures": fixtures, "unrequestedFixtures": sorted(set(inventory) - set(fixtures)),
            "timingQualified": False, "releaseQualified": False,
            "samplingNote": "Campaign lists identify samples, not recorded execution order. Quiet paired sampling is not certified.",
            "statistics": "Operation only; median and nearest-rank P95. Positive saved values mean lower raw latency.",
            "fixtures": rows}


def markdown(data: dict) -> str:
    lines = ["# Previous/current runtime observations", "",
             "These are retained observations, not a quiet benchmark or new stable-release qualification. "
             "A failed baseline is a functional difference, never a speedup. " + data["samplingNote"], "",
             "| Fixture | Role | Product | Samples | Eligible | Median s | P95 s |",
             "| --- | --- | --- | ---: | --- | ---: | ---: |"]
    for row in data["fixtures"]:
        for role in ("baseline", "target"):
            group = row[role]
            product = group["product"]
            label = product.get("version", product["sourceCommit"][:12]) + " (" + product["kind"] + ")"
            times = group["summary"]
            values = " | ".join(f"{times[key] / 1e9:.9f}" if times else "not compared" for key in ("medianNS", "p95NS"))
            lines.append(f"| {row['fixture']} | {role} | {label} | {len(group['samples'])} | {str(group['eligible']).lower()} | {values} |")
    for row in data["fixtures"]:
        lines += ["", "## " + row["fixture"]]
        if row["rawDifference"] is not None:
            change = row["rawDifference"]["medianNS"]
            lines += ["", f"{row['fixture']}: raw median difference {change['savedNS'] / 1e9:+.9f} s saved "
                      f"({change['savedPercent']:+.3f}%); not a qualified improvement claim."]
        lines += ["", "| Role | Campaign | Status | Setup s | Operation s | Cleanup s | Cleanup |",
                  "| --- | --- | --- | ---: | ---: | ---: | --- |"]
        for role in ("baseline", "target"):
            for sample in row[role]["samples"]:
                times = " | ".join(f"{sample['durationsNS'][phase] / 1e9:.9f}" for phase in ("setup", "operation", "cleanup"))
                lines.append(f"| {role} | {sample['identity']['campaign']} | {sample['status']} | {times} | {sample['cleanupStatus']} |")
        lines += [""]
    lines += ["Exact product digests, source commits, runtime/harness fingerprints, all samples and evidence seals are in the JSON report.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="append", required=True)
    parser.add_argument("--target", action="append", required=True)
    parser.add_argument("--fixture", action="append", required=True)
    parser.add_argument("--lane", choices=("apple-stock", "container-compose"), required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    retained = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if retained.resolve() != retained or retained.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("Evidence must remain on internal home storage")
    data = compare(retained / "runtime-cases.sqlite", Path(__file__).resolve().parents[2],
                   args.baseline, args.target, args.fixture, args.lane)
    print(markdown(data) if args.format == "markdown" else json.dumps(data, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
