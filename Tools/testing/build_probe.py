"""Pure E04 build inputs and observations; never create or delete runtime state.

Live callers must first admit the builder, pinned base image and owned runtime,
journal image intent, then verify the resulting identity and cleanup. These
helpers alone do not qualify the image-build fixture or a failed RUN phase.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import re
import tarfile
from urllib.parse import urlencode

from case_evidence import canonical
from guest_fixture import OWNER_LABEL


MAX_BUILD_OUTPUT = 8 * 1024**2


def tag_for(owner: str, *, failing: bool = False) -> str:
    if not isinstance(owner, str) or re.fullmatch(r"[0-9a-f]{64}", owner) is None:
        raise ValueError("Build fixture requires an immutable owner identity")
    return "cf-test-" + owner + (":failed" if failing else ":built")


def build_context(base: str, owner: str, *, failing: bool = False) -> bytes:
    """Keep the original ARG/RUN/label contract with a digest-pinned base."""
    tag_for(owner, failing=failing)
    if (not isinstance(base, str) or
            re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", base) is None):
        raise ValueError("Build base must be a digest-pinned image reference")
    instructions = (f"FROM {base}\nARG PARITY_VALUE\n"
                    f'LABEL {OWNER_LABEL}="{owner}"\n')
    instructions += ("RUN false\n" if failing else
                     'RUN test "$PARITY_VALUE" = expected\nLABEL devcontainer.parity="true"\nCMD ["true"]\n')
    payload = instructions.encode("ascii")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo("Dockerfile")
        member.mode, member.size = 0o644, len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def build_route(owner: str, *, failing: bool = False) -> str:
    """Unversioned route; caller supplies the same admitted API version per lane."""
    return "/build?" + urlencode({"t": tag_for(owner, failing=failing), "dockerfile": "Dockerfile",
                                  "buildargs": canonical({"PARITY_VALUE": "expected"}).decode(),
                                  "pull": "false", "rm": "true", "forcerm": "true"})


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate build response field")
        value[key] = item
    return value


@dataclass(frozen=True)
class BuildOutput:
    progress: bool
    failed: bool
    records: int


def progress_record(value: dict) -> bool:
    progress = False
    for key in ("stream", "status"):
        if key in value:
            if not isinstance(value[key], str):
                raise ValueError("Build progress is not text")
            progress = progress or bool(value[key].strip())
    return progress


def error_record(value: dict) -> bool:
    if "error" not in value and "errorDetail" not in value:
        return False
    detail = value.get("errorDetail", {})
    error = value.get("error", detail.get("message") if isinstance(detail, dict) else None)
    if (not isinstance(detail, dict) or not isinstance(error, str) or not error.strip() or
            ("message" in detail and (not isinstance(detail["message"], str) or not detail["message"].strip()))):
        raise ValueError("Malformed build error record")
    return True


def build_output(status: int, payload: bytes) -> BuildOutput:
    """HTTP 200 can contain build errors; HTTP rejection is not a failed RUN."""
    if status != 200:
        raise ValueError("Build request was rejected before a qualified build result")
    if not payload or len(payload) > MAX_BUILD_OUTPUT:
        raise ValueError("Build response is empty or exceeds the fixture bound")
    progress, failed, count = False, False, 0
    for line in payload.splitlines():
        if not line.strip():
            continue
        value = json.loads(line, object_pairs_hook=unique_object)
        if not isinstance(value, dict) or not value:
            raise ValueError("Build response record is not a nonempty object")
        count += 1
        # Validate every record even after progress or failure has been observed.
        progress = progress_record(value) or progress
        failed = error_record(value) or failed
    if count == 0:
        raise ValueError("Build response has no records")
    return BuildOutput(progress, failed, count)


def owned_image(value: dict, owner: str, *, failing: bool = False) -> dict:
    """Require one exact owned tag before a later caller may request deletion."""
    tag = tag_for(owner, failing=failing)
    identifier = value.get("Id")
    config = value.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (not isinstance(identifier, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", identifier) is None or
            value.get("RepoTags") != [tag] or not isinstance(labels, dict) or labels.get(OWNER_LABEL) != owner):
        raise ValueError("Build image ownership or identity differs")
    return {"id": identifier, "tag": tag, "owner": owner,
            "inspect_label": labels.get("devcontainer.parity") == "true"}
