"""Real Unix HTTP and adversarial archive tests; no runtime or Docker CLI."""

from http.server import BaseHTTPRequestHandler
from copy import copy
import io
import json
import os
from pathlib import Path
from socketserver import UnixStreamServer
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

from archive_probe import LIMIT, archive_copy, observations, source_archive
from engine_probe import request


def changed_archive(change):
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(source_archive()), mode="r:") as source, \
            tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as target:
        for member in source:
            data = source.extractfile(member).read() if member.isfile() else None
            for entry, content in change(member, data):
                target.addfile(entry, io.BytesIO(content) if content is not None else None)
    return output.getvalue()


class Handler(BaseHTTPRequestHandler):
    def do_PUT(self):
        self.server.upload = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.content_type = self.headers["Content-Type"]
        self.respond(b"")

    def do_GET(self):
        self.respond(self.server.response)

    def respond(self, body):
        self.server.routes.append((self.command, self.path))
        self.send_response(self.server.status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        # Assertions below own the traffic evidence; do not print payloads.
        pass


class ArchiveProbeTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(scratch.cleanup)
        self.socket = Path(scratch.name) / "s"
        self.server = UnixStreamServer(str(self.socket), Handler)
        self.server.response = source_archive()
        self.server.status, self.server.routes = 200, []
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.stop)
        self.identifier = "a" * 64

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def test_same_five_contract_observations_over_actual_unix_http(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/E05-archive-copy/contract.json"
        expected = {key: str(value).lower() for key, value in json.loads(contract.read_text())["expected"].items()}
        events = []
        self.assertEqual(archive_copy(self.socket, self.identifier, "1.54", observe=events.append), expected)
        self.assertEqual(self.server.upload, source_archive())
        self.assertEqual(self.server.content_type, "application/x-tar")
        prefix = f"/v1.54/containers/{self.identifier}/archive?path="
        self.assertEqual(self.server.routes, [("PUT", prefix + "/tmp"), ("GET", prefix + "/tmp/archive")])
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertEqual(set(event), {"method", "route", "status", "durationNS"})
            self.assertGreaterEqual(event["durationNS"], 0)

    def test_deterministic_archive_retains_exact_mode_not_just_permissions(self):
        self.assertEqual(source_archive(), source_archive())
        def alter(member, data):
            if member.name == "archive/regular.txt":
                member.mode = 0o4750
            return [(member, data)]
        self.assertEqual(observations(changed_archive(alter))["mode"], "0o4750")

    def test_wrong_bytes_are_not_normalized_and_other_observations_survive(self):
        def alter(member, data):
            if member.name == "archive/large.bin":
                data = b"x" * len(data)
            return [(member, data)]
        result = observations(changed_archive(alter))
        self.assertEqual(result["large_file"], "false")
        self.assertEqual(result["content"], "true")

    def test_wrong_length_and_nonregular_file_do_not_follow_links(self):
        for kind in ("length", "link"):
            def alter(member, data):
                if member.name == "archive/regular.txt":
                    member.size = 0
                    data = b""
                    if kind == "link":
                        member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
                return [(member, data)]
            with self.subTest(kind=kind):
                self.assertEqual(observations(changed_archive(alter))["content"], "false")

    def test_wrong_link_target_or_type_is_not_accepted(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            def alter(member, data):
                if member.name == "archive/regular-link":
                    member.type, member.linkname = kind, "../regular.txt"
                return [(member, data)]
            with self.subTest(kind=kind):
                self.assertEqual(observations(changed_archive(alter))["symlink"], "false")

    def test_unsafe_paths_duplicate_expected_paths_and_missing_files_fail(self):
        for kind in ("traversal", "duplicate", "missing"):
            def alter(member, data):
                if member.name != "archive/regular.txt":
                    return [(member, data)]
                if kind == "missing":
                    return []
                if kind == "duplicate":
                    return [(member, data), (member, data)]
                if kind == "traversal":
                    member.name = "../escape"
                return [(member, data)]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                observations(changed_archive(alter))

    def test_optional_directory_and_unrelated_safe_members_do_not_change_contract(self):
        expected = observations(source_archive())
        for kind in ("no-directory", "extra-file"):
            def alter(member, data):
                if member.name != "archive":
                    return [(member, data)]
                if kind == "no-directory":
                    return []
                extra = tarfile.TarInfo("archive/unrelated.txt")
                extra.size = 5
                return [(member, data), (extra, b"extra")]
            with self.subTest(kind=kind):
                self.assertEqual(observations(changed_archive(alter)), expected)

    def test_equivalent_path_names_match_and_cannot_hide_duplicate_expected_files(self):
        def prefixed(member, data):
            member.name = "./" + member.name
            return [(member, data)]
        self.assertEqual(observations(changed_archive(prefixed)), observations(source_archive()))
        for name in ("./archive/regular.txt", "archive//regular.txt"):
            def duplicate(member, data):
                if member.name != "archive/regular.txt":
                    return [(member, data)]
                alias = copy(member)
                alias.name = name
                return [(member, data), (alias, b"x" * len(data))]
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Duplicate expected"):
                observations(changed_archive(duplicate))

    def test_malformed_and_oversized_archive_fail_closed(self):
        with self.assertRaises(tarfile.ReadError):
            observations(b"not a tar archive")
        with self.assertRaisesRegex(ValueError, "limit"):
            observations(b"x" * (LIMIT + 1))

    def test_invalid_identity_fails_before_any_request(self):
        for identifier, version in (("short", "1.54"), (self.identifier, "../escape")):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                archive_copy(self.socket, identifier, version)
        self.assertEqual(self.server.routes, [])

    def test_upload_error_stops_before_download_and_retains_status(self):
        self.server.status = 403
        events = []
        with self.assertRaisesRegex(ValueError, "operation failed"):
            archive_copy(self.socket, self.identifier, "1.54", observe=events.append)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], 403)
        self.assertEqual(events[0]["error"], "ValueError")

    def test_timeout_and_interrupt_keep_sanitized_timing_evidence(self):
        for error in (TimeoutError("secret"), KeyboardInterrupt("secret")):
            events = []
            with patch("archive_probe.request", side_effect=error), self.assertRaises(type(error)):
                archive_copy(self.socket, self.identifier, "1.54", observe=events.append)
            self.assertEqual(events[0]["error"], type(error).__name__)
            self.assertNotIn("secret", json.dumps(events))
            self.assertGreaterEqual(events[0]["durationNS"], 0)

    def test_response_budget_is_enforced_at_transport_boundary(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            request(self.socket, "GET", "/fixture", max_bytes=len(self.server.response) - 1)
        for invalid in (0, -1, True, 16 * 1024**2 + 1):
            with self.subTest(limit=invalid), self.assertRaisesRegex(ValueError, "limit"):
                request(self.socket, "GET", "/fixture", max_bytes=invalid)


if __name__ == "__main__":
    unittest.main()
