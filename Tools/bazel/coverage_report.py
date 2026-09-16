"""Export retained native coverage without invoking Bazel or rerunning tests."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import xml.etree.ElementTree as ET

from input_identity import verify
from retain_evidence import digest


def sonar_xml(lcov: bytes) -> tuple[bytes, int, int]:
    """Convert the exact first-party LCOV line denominator, without exclusions."""
    files = {}
    for record in lcov.decode("utf-8").split("end_of_record"):
        rows = record.strip().splitlines()
        if not rows:
            continue
        sources = [row[3:] for row in rows if row.startswith("SF:")]
        if len(sources) != 1:
            raise ValueError("Coverage record needs exactly one source")
        source = sources[0]
        path = PurePosixPath(source)
        if (not source.startswith("Sources/") or path.as_posix() != source
                or ".." in path.parts or "\\" in source or source in files):
            raise ValueError("Coverage source is duplicate or outside the maintained source tree")
        lines = {}
        for row in rows:
            if not row.startswith("DA:"):
                continue
            fields = row[3:].split(",")
            if len(fields) not in {2, 3} or not all(re.fullmatch(r"[0-9]+", field) for field in fields[:2]):
                raise ValueError("Invalid LCOV line counts")
            line, hits = map(int, fields[:2])
            if line <= 0 or line in lines:
                raise ValueError("Invalid or duplicate LCOV line")
            lines[line] = hits
        found = [row[3:] for row in rows if row.startswith("LF:")]
        hit = [row[3:] for row in rows if row.startswith("LH:")]
        if found != [str(len(lines))] or hit != [str(sum(count > 0 for count in lines.values()))]:
            raise ValueError("LCOV summary disagrees with measured lines")
        files[source] = lines
    root = ET.Element("coverage", {"version": "1"})
    total, covered = 0, 0
    for source, lines in sorted(files.items()):
        if not lines:
            continue
        element = ET.SubElement(root, "file", {"path": source})
        for line, hits in sorted(lines.items()):
            ET.SubElement(element, "lineToCover", {
                "lineNumber": str(line), "covered": "true" if hits > 0 else "false",
            })
            total += 1
            covered += hits > 0
    if not 0 < covered <= total:
        raise ValueError("Empty or unexecuted coverage")
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), covered, total


def report_bytes(database: Path, invocation: str) -> dict[str, bytes]:
    """Read only authenticated coverage/provenance, never raw event environments."""
    if database.is_symlink() or not database.is_file():
        raise ValueError("Missing or symlinked evidence database")
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as db:
        row = db.execute("SELECT manifest, exit_code FROM invocations WHERE id=?", (invocation,)).fetchone()
        if row is None or row[1] != 0:
            raise ValueError("Coverage requires a successful retained invocation")
        manifest = json.loads(row[0])

        def read(name: str) -> bytes:
            expected = manifest.get(name)
            blob = db.execute("SELECT bytes FROM blobs WHERE sha256=?", (expected,)).fetchone()
            if blob is None or digest(blob[0]) != expected:
                raise ValueError("Missing or corrupt retained coverage evidence")
            return blob[0]

        before = json.loads(read("inputs-before.json"))
        after = json.loads(read("inputs-after.json"))
        verify(before, after)
        if (before.get("dirty") is not False or after.get("dirty") is not False
                or not re.fullmatch(r"[0-9a-f]{40}", before.get("commit", ""))):
            raise ValueError("Coverage export requires clean recorded source identity")
        outcome = json.loads(read("outcome.json"))
        if outcome != {"bazel_exit_code": 0, "validation_exit_code": 0, "suite": "source"}:
            raise ValueError("Coverage needs the validated complete source unit suite")
        report = json.loads(read("source-tests.json"))
        lcov = read("build:build:coverage_report.lcov")
        xml, covered, total = sonar_xml(lcov)
        counts = report["coverage"]
        if (counts["sha256"] != digest(lcov) or counts["hit"] != covered or counts["found"] != total
                or report.get("runtime_profile") not in {"stock", "enhanced"}):
            raise ValueError("Coverage does not match the validated invocation")
    receipt = {
        "schema": 1, "invocation": invocation, "sourceCommit": before["commit"],
        "runtimeProfile": report["runtime_profile"], "scope": counts["scope"],
        "sourceIdentitySHA256": digest(json.dumps(before, sort_keys=True).encode()),
        "lcovSHA256": digest(lcov), "sonarXMLSHA256": digest(xml),
        "coveredLines": covered, "measuredLines": total,
        "percent": round(100 * covered / total, 4), "releaseAuthority": False,
    }
    return {"coverage.lcov": lcov, "coverage.xml": xml,
            "receipt.json": (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()}


def export(database: Path, invocation: str, scratch: Path) -> Path:
    """Publish or reuse an exact disposable report from durable input bytes."""
    contents = report_bytes(database, invocation)
    parent = scratch / "coverage"
    parent.mkdir(exist_ok=True)
    if parent.resolve() != parent:
        raise ValueError("Refusing symlinked coverage destination")
    destination = parent / digest(invocation.encode())
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir() or {p.name for p in destination.iterdir()} != set(contents):
            raise ValueError("Conflicting coverage destination")
        for name, data in contents.items():
            path = destination / name
            if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                raise ValueError("Conflicting exported coverage bytes")
        return destination
    with tempfile.TemporaryDirectory(dir=scratch / "tmp", prefix="coverage-") as temporary:
        stage = Path(temporary) / "report"
        stage.mkdir(mode=0o700)
        for name, data in contents.items():
            (stage / name).write_bytes(data)
        stage.rename(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("invocation")
    args = parser.parse_args()
    root = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if root.resolve() != root or root.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("Evidence must be on non-symlinked internal storage")
    os.umask(0o077)
    print(export(root / "bazel-evidence.sqlite", args.invocation, Path("/Volumes/SSD/cf/bazel")))


if __name__ == "__main__":
    main()
