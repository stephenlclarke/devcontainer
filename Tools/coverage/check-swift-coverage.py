#!/usr/bin/env python3
"""Enforce aggregate first-party Swift line coverage."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def is_first_party_source(filename: str) -> bool:
    """Return whether an LLVM coverage filename is project production source."""

    normalized = filename.replace("\\", "/")
    return "/Sources/" in normalized and "/.build/" not in normalized


def display_path(filename: str, source_root: Path) -> str:
    """Return a stable project-relative path when the source is in the repository."""

    path = Path(filename)
    try:
        return path.resolve().relative_to(source_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def execution_lines(path: Path, source_root: Path) -> dict[str, dict[int, int]]:
    """Load first-party executable lines keyed by stable source path."""

    document = json.loads(path.read_text(encoding="utf-8"))
    execution_by_file: dict[str, dict[int, int]] = {}
    for datum in document.get("data", []):
        for item in datum.get("files", []):
            filename = item.get("filename", "")
            if not isinstance(filename, str):
                continue
            normalized = filename.replace("\\", "/")
            if not is_first_party_source(normalized):
                continue
            target = execution_by_file.setdefault(
                display_path(normalized, source_root),
                {},
            )
            for line, execution_count in sonar_lines(item).items():
                target[line] = max(target.get(line, 0), execution_count)
    return execution_by_file


def coverage(path: Path) -> tuple[int, int, list[tuple[str, float]]]:
    """Count unique executable source lines using the Sonar export semantics."""

    execution_by_file = execution_lines(path, Path.cwd())
    covered = 0
    count = 0
    files: list[tuple[str, float]] = []
    for filename, lines in execution_by_file.items():
        if not lines:
            continue
        file_count = len(lines)
        file_covered = sum(execution_count > 0 for execution_count in lines.values())
        covered += file_covered
        count += file_count
        files.append((filename, file_covered * 100.0 / file_count))
    return covered, count, sorted(files, key=lambda value: value[1])


def sonar_lines(item: dict[str, object]) -> dict[int, int]:
    """Return executable source lines and their greatest execution count.

    LLVM segments describe coverage state from one source location until the
    next. A later executable segment on a new line may therefore begin after
    code already ran on that line. Preserve the segment-entry line set used by
    Sonar, but include the active count entering each such line.
    """

    lines: dict[int, int] = {}
    raw_segments = item.get("segments", [])
    if not isinstance(raw_segments, list):
        return lines
    segments: list[tuple[int, int, int, bool, bool]] = []
    for raw in raw_segments:
        if not isinstance(raw, list) or len(raw) < 6:
            continue
        line, column, execution_count, has_count, _, is_gap = raw[:6]
        if (
            not isinstance(line, int)
            or line <= 0
            or not isinstance(column, int)
            or column <= 0
            or not isinstance(execution_count, int)
            or not isinstance(has_count, bool)
            or not isinstance(is_gap, bool)
        ):
            continue
        segments.append((line, column, execution_count, has_count, is_gap))
        if has_count and not is_gap:
            lines[line] = max(lines.get(line, 0), execution_count)

    segments.sort(key=lambda value: (value[0], value[1]))
    active: tuple[int, int, int, bool, bool] | None = None
    index = 0
    for line in sorted(lines):
        while index < len(segments) and segments[index][:2] < (line, 1):
            active = segments[index]
            index += 1
        if active is not None and active[3] and not active[4]:
            lines[line] = max(lines[line], active[2])
    return lines


def write_lcov_report(report: Path, output: Path, source_root: Path) -> None:
    """Write first-party LCOV using the same unique-line semantics as the gate."""

    records: list[str] = []
    for filename, lines in sorted(execution_lines(report, source_root).items()):
        if not lines:
            continue
        covered = sum(execution_count > 0 for execution_count in lines.values())
        records.extend(
            [
                "TN:devcontainer",
                f"SF:{filename}",
                *(f"DA:{line},{execution_count}" for line, execution_count in sorted(lines.items())),
                f"LF:{len(lines)}",
                f"LH:{covered}",
                "end_of_record",
            ]
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(records) + "\n", encoding="utf-8")


def write_sonar_report(report: Path, output: Path, source_root: Path) -> None:
    """Write SonarQube generic line-coverage XML from LLVM JSON."""

    root = ET.Element("coverage", {"version": "1"})
    for filename, lines in sorted(execution_lines(report, source_root).items()):
        if not lines:
            continue
        file_element = ET.SubElement(root, "file", {"path": filename})
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


def diff_changed_lines(diff: str) -> dict[str, set[int]]:
    """Parse added-line ranges from a zero-context Git diff."""

    changed: dict[str, set[int]] = {}
    current: str | None = None
    hunk_pattern = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ "):
            fields = shlex.split(raw_line[4:])
            if len(fields) != 1 or fields[0] == "/dev/null":
                current = None
                continue
            current = fields[0].removeprefix("b/")
            if current.startswith("Sources/") and current.endswith(".swift"):
                changed.setdefault(current, set())
            else:
                current = None
            continue
        if current is None:
            continue
        match = hunk_pattern.match(raw_line)
        if match is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        changed[current].update(range(start, start + count))
    return changed


def git_changed_lines(
    repository: Path,
    base_ref: str,
    head_ref: str,
) -> tuple[str, dict[str, set[int]]]:
    """Return the merge base and changed Swift source lines for a Git range."""

    if not base_ref or set(base_ref) == {"0"}:
        raise ValueError("coverage comparison ref is empty or the all-zero Git ref")
    command_options = {
        "cwd": repository,
        "check": True,
        "text": True,
        "capture_output": True,
    }
    merge_base = subprocess.run(
        ["git", "merge-base", "--", base_ref, head_ref],
        **command_options,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", merge_base):
        raise ValueError(f"Git returned an invalid merge base: {merge_base!r}")
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--unified=0",
            "--no-color",
            "--no-ext-diff",
            "--find-renames",
            merge_base,
            head_ref,
            "--",
            "Sources",
        ],
        **command_options,
    ).stdout
    return merge_base, diff_changed_lines(diff)


def changed_coverage(
    report: Path,
    source_root: Path,
    changed: dict[str, set[int]],
) -> tuple[int, int, list[tuple[str, int]], list[str]]:
    """Count coverage for changed executable lines and report missing sources."""

    by_file = execution_lines(report, source_root)
    covered = 0
    count = 0
    uncovered: list[tuple[str, int]] = []
    missing: list[str] = []
    for filename, changed_lines in sorted(changed.items()):
        if filename not in by_file:
            missing.append(filename)
            continue
        for line in sorted(changed_lines.intersection(by_file[filename])):
            count += 1
            if by_file[filename][line] > 0:
                covered += 1
            else:
                uncovered.append((filename, line))
    return covered, count, uncovered, missing


def annotate_uncovered(uncovered: list[tuple[str, int]]) -> None:
    """Emit GitHub source annotations for uncovered changed executable lines."""

    if not os.getenv("GITHUB_ACTIONS"):
        return
    for filename, line in uncovered:
        print(
            f"::error file={filename},line={line},"
            "title=Changed line is not covered::"
            "Add a test that executes this changed production line."
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimum", type=float, default=90.0)
    parser.add_argument("--changed-minimum", type=float)
    parser.add_argument("--changed-since")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--lcov-output", type=Path)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--sonar-output", type=Path)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("report", type=Path)
    args = parser.parse_args()

    if args.changed_minimum is None:
        args.changed_minimum = args.minimum
    if args.lcov_output:
        write_lcov_report(args.report, args.lcov_output, args.source_root)
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
    if args.changed_since:
        try:
            merge_base, changed = git_changed_lines(
                (args.repository or args.source_root).resolve(),
                args.changed_since,
                args.head_ref,
            )
        except (subprocess.CalledProcessError, ValueError) as error:
            print(f"could not determine changed coverage range: {error}", file=sys.stderr)
            return 2
        changed_covered, changed_count, uncovered, missing = changed_coverage(
            args.report,
            args.source_root,
            changed,
        )
        if missing:
            print(
                "changed Swift sources are absent from the coverage report: "
                + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        if changed_count == 0:
            print(
                "Swift changed executable-line coverage: not applicable "
                f"(0 executable lines; merge base {merge_base})"
            )
            return 0
        changed_actual = changed_covered * 100.0 / changed_count
        print(
            "Swift changed executable-line coverage: "
            f"{changed_actual:.2f}% ({changed_covered}/{changed_count}; "
            f"merge base {merge_base})"
        )
        for filename, line in uncovered:
            print(f"  uncovered {filename}:{line}")
        annotate_uncovered(uncovered)
        if changed_actual + 1e-9 < args.changed_minimum:
            print(
                "changed-line coverage is below required "
                f"{args.changed_minimum:.2f}%",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
