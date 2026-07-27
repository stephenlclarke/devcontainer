#!/usr/bin/env python3
"""Enforce aggregate first-party Swift line coverage."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def is_first_party_source(filename: str) -> bool:
    """Return whether an LLVM coverage filename is project production source."""

    normalized = filename.replace("\\", "/")
    return "/Sources/" in normalized and "/.build/" not in normalized


def coverage(path: Path) -> tuple[int, int, list[tuple[str, float]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    covered = 0
    count = 0
    files: list[tuple[str, float]] = []
    for datum in document.get("data", []):
        for item in datum.get("files", []):
            filename = item.get("filename", "")
            normalized = filename.replace("\\", "/")
            if not is_first_party_source(normalized):
                continue
            lines = item.get("summary", {}).get("lines", {})
            file_count = int(lines.get("count", 0))
            file_covered = int(lines.get("covered", 0))
            if file_count == 0:
                continue
            covered += file_covered
            count += file_count
            files.append((normalized, file_covered * 100.0 / file_count))
    return covered, count, sorted(files, key=lambda value: value[1])


def sonar_lines(item: dict[str, object]) -> dict[int, int]:
    """Return executable source lines and their greatest execution count."""

    lines: dict[int, int] = {}
    raw_segments = item.get("segments", [])
    if not isinstance(raw_segments, list):
        return lines
    for raw in raw_segments:
        if not isinstance(raw, list) or len(raw) < 6:
            continue
        line, _, execution_count, has_count, _, is_gap = raw[:6]
        if (
            not isinstance(line, int)
            or line <= 0
            or not isinstance(execution_count, int)
            or has_count is not True
            or is_gap is True
        ):
            continue
        lines[line] = max(lines.get(line, 0), execution_count)
    return lines


def write_sonar_report(report: Path, output: Path, source_root: Path) -> None:
    """Write SonarQube generic line-coverage XML from LLVM JSON."""

    document = json.loads(report.read_text(encoding="utf-8"))
    root = ET.Element("coverage", {"version": "1"})
    files: list[tuple[str, dict[int, int]]] = []
    resolved_root = source_root.resolve()
    for datum in document.get("data", []):
        for item in datum.get("files", []):
            filename = item.get("filename", "")
            if not isinstance(filename, str) or not is_first_party_source(filename):
                continue
            path = Path(filename)
            try:
                display_path = path.resolve().relative_to(resolved_root).as_posix()
            except ValueError:
                display_path = path.as_posix()
            lines = sonar_lines(item)
            if lines:
                files.append((display_path, lines))
    for display_path, lines in sorted(files):
        file_element = ET.SubElement(root, "file", {"path": display_path})
        for line, execution_count in sorted(lines.items()):
            ET.SubElement(
                file_element,
                "lineToCover",
                {
                    "lineNumber": str(line),
                    "covered": "true" if execution_count > 0 else "false",
                },
            )
    ET.indent(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimum", type=float, default=90.0)
    parser.add_argument("--sonar-output", type=Path)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("report", type=Path)
    args = parser.parse_args()

    if args.sonar_output:
        write_sonar_report(args.report, args.sonar_output, args.source_root)
    covered, count, files = coverage(args.report)
    actual = covered * 100.0 / count if count else 0.0
    print(f"Swift first-party line coverage: {actual:.2f}% ({covered}/{count})")
    for filename, percentage in files[:10]:
        print(f"  {percentage:6.2f}%  {filename}")
    if actual + 1e-9 < args.minimum:
        print(
            f"coverage is below required {args.minimum:.2f}%",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
