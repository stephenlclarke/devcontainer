"""Real Unix-socket races and negative evidence for the F01 adapter."""

import json
from pathlib import Path
from socketserver import ThreadingUnixStreamServer
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fault_probe import COMMAND, FaultFixture, ScopedJournal, concurrent_requests, fault_recovery
from guest_fixture import GuestFixture
import test_guest_fixture as helpers


class Handler(helpers.Handler):
    def dispatch(self):
        server = self.server
        route = urlsplit(self.path)
        verb = route.path.rsplit("/", 1)[-1]
        resource = route.path.split("/")[3] if route.path.startswith("/v1.54/containers/") else None
        if (self.command == "POST" and verb == "start") or self.command == "DELETE":
            if self.command == "POST" or resource == "c" * 64:
                server.barrier.wait(timeout=5)
            with server.lock:
                server.routes.append((self.command, self.path))
                guest = server.guests.get(resource)
                if self.command == "DELETE":
                    if server.remove_status is not None:
                        return server.remove_status, server.remove_body
                    if guest is None:
                        return 404, {"message": "absent"}
                    if not server.ignore_delete:
                        del server.guests[resource]
                    return 204, b""
                if server.start_status is not None:
                    return server.start_status, b""
                running = guest["State"]["Status"] == "running"
                guest["State"]["Status"] = "running"
                return (304 if running else 204), b""
        if self.command == "GET" and verb == "logs":
            payload = b"ready-1\n"
            return 200, b"\1\0\0\0" + len(payload).to_bytes(4, "big") + payload
        if self.command == "POST" and verb == "kill":
            server.routes.append((self.command, self.path))
            if server.kill_status == 204:
                server.guest["State"] = {"Status": "exited", "ExitCode": server.exit_code}
            return server.kill_status, b""
        if self.command == "POST" and verb == "wait":
            if server.replaced:
                server.guest["Id"] = "d" * 64
            return server.wait_status, server.wait_result
        if self.command == "GET" and verb == "events":
            server.event_query = parse_qs(route.query)
            return server.event_status, server.event_body
        with server.lock:
            server.routes.append((self.command, self.path))
            if self.command == "GET" and "/images/" in route.path:
                return 200, {"Id": server.image}
            if self.command == "POST" and verb == "create":
                body = self.rfile.read(int(self.headers["Content-Length"]))
                config = json.loads(body)
                identifier = ("c" if config["Cmd"] == ["true"] else "b") * 64
                value = {"Id": identifier, "Name": "/" + parse_qs(route.query)["name"][0], "Config": config,
                         "Image": server.image, "State": {"Status": "created"}}
                server.guests[identifier] = value
                if config["Cmd"] != ["true"]:
                    server.guest = value
                return 201, {"Id": identifier}
            if route.path == "/v1.54/containers/json":
                labels = json.loads(parse_qs(route.query)["filters"][0])["label"]
                owned = [value for value in server.guests.values() if all(
                    value["Config"]["Labels"].get(key) == expected for key, expected in (item.split("=", 1) for item in labels))]
                return 200, owned
            values = [value for key, value in server.guests.items() if resource in {key, value["Name"][1:]}]
            return (200, values[0]) if values else (404, {"message": "absent"})


class FaultTests(unittest.TestCase):
    reopen = helpers.GuestFixtureTests.reopen
    stop = helpers.GuestFixtureTests.stop

    def setUp(self):
        with patch.object(helpers, "Handler", Handler), patch.object(helpers, "UnixStreamServer", ThreadingUnixStreamServer):
            helpers.GuestFixtureTests.setUp(self)
        self.events = []
        self.case = FaultFixture(self.socket, self.owner, self.server.image, "1.54", self.journal, observe=self.events.append)
        self.fixture, self.removal = self.case.guests
        self.server.guests = {}
        self.server.lock = threading.Lock()
        self.server.barrier = threading.Barrier(4, timeout=5)
        self.server.exit_code, self.server.kill_status = 42, 204
        self.server.wait_status, self.server.wait_result = 200, {"StatusCode": 42, "Error": None}
        self.server.start_status, self.server.remove_status = None, None
        self.server.remove_body = {"message": "conflict"}
        self.server.event_status, self.server.event_body = 200, b""
        self.server.replaced = False

    def test_original_contract_with_four_simultaneous_requests_and_cleanup(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/F01-fault-recovery/contract.json"
        expected = {key: str(value).lower() for key, value in json.loads(contract.read_text())["expected"].items()}
        self.assertEqual(self.case.operation(), expected)
        self.assertEqual(self.case.cleanup(), {"status": "passed", "remainingOwnedResources": []})
        records = self.journal.records()
        self.assertEqual([item["status"] for item in json.loads(records["f01-signal-f01-start-results.json"])].count(204), 1)
        self.assertEqual([item["status"] for item in json.loads(records["f01-remove-f01-remove-results.json"])].count(204), 1)
        self.assertEqual(sum(method == "DELETE" for method, _ in self.server.routes), 5)
        self.assertFalse(any(path.endswith("/" + "c" * 64 + "/start") for _, path in self.server.routes))
        exited = json.loads(records['f01-signal-f01-inspect-exited.json'])
        self.assertEqual(exited['State'], {'Status': 'exited', 'ExitCode': 42})
        self.assertEqual(set(exited), {'Id', 'Image', 'State'})
        self.assertTrue(all(event["durationNS"] >= 0 for event in self.events))
        self.assertEqual(int(self.server.event_query["until"][0]) - int(self.server.event_query["since"][0]), 1)
        self.assertEqual(json.loads(self.server.event_query["filters"][0]), {"event": ["devcontainer-never"]})

    def test_command_mismatch_refused_before_creation(self):
        ordinary = self.reopen()
        with self.assertRaisesRegex(ValueError, "signal-aware"):
            fault_recovery(ordinary, self.removal)
        self.assertEqual(self.server.routes, [])

    def test_failed_start_batch_is_not_retried_or_signalled(self):
        self.server.start_status = 500
        with self.assertRaisesRegex(ValueError, "state changed"):
            self.case.operation()
        self.assertEqual(sum(path.endswith("/start") for _, path in self.server.routes), 4)
        self.assertFalse(any("/kill" in path for _, path in self.server.routes))

    def test_one_start_error_stays_false_even_when_container_started(self):
        original = concurrent_requests
        def failed_one(guest, method, route):
            results = original(guest, method, route)
            return [(500, b"error"), *results[1:]] if method == "POST" else results
        with patch("fault_probe.concurrent_requests", side_effect=failed_one):
            self.assertEqual(self.case.operation()["concurrent_start"], "false")

    def test_signal_requires_exact_wait_and_inspected_exit(self):
        self.server.exit_code = 143
        self.assertEqual(self.case.operation()["signal_exit"], "false")

    def test_wrong_wait_code_is_not_normalized(self):
        self.server.wait_result = {"StatusCode": 143}
        self.assertEqual(self.case.operation()["signal_exit"], "false")

    def test_signal_failure_is_not_retried(self):
        self.server.kill_status = 500
        with self.assertRaisesRegex(ValueError, "TERM"):
            self.case.operation()
        self.assertEqual(sum("/kill" in path for _, path in self.server.routes), 1)

    def test_invalid_wait_is_rejected_before_removal(self):
        self.server.wait_result = {"StatusCode": True}
        with self.assertRaisesRegex(ValueError, "wait failed"):
            self.case.operation()
        self.assertNotIn("f01-remove-container-delete-intent.json", self.journal.records())

    def test_replaced_identity_is_not_removed(self):
        self.server.replaced = True
        with self.assertRaisesRegex(ValueError, "ownership|identity"):
            self.case.operation()
        self.assertNotIn("f01-remove-container-delete-intent.json", self.journal.records())

    def test_all_remove_conflicts_do_not_pass(self):
        self.server.remove_status = 409
        self.assertEqual(self.case.operation()["remove_race"], "false")

    def test_remove_success_without_absence_does_not_pass(self):
        self.server.ignore_delete = True
        self.assertEqual(self.case.operation()["remove_race"], "false")

    def test_invalid_remove_response_does_not_pass(self):
        self.server.remove_status = 500
        self.assertEqual(self.case.operation()["remove_race"], "false")

    def test_remove_error_must_have_docker_envelope(self):
        original = concurrent_requests
        def malformed_one(guest, method, route):
            results = original(guest, method, route)
            return [(404, b"{}"), *results[1:]] if method == "DELETE" else results
        with patch("fault_probe.concurrent_requests", side_effect=malformed_one):
            self.assertEqual(self.case.operation()["remove_race"], "false")

    def test_event_error_is_not_success(self):
        self.server.event_status = 500
        self.assertEqual(self.case.operation()["bounded_events"], "false")

    def test_unexpected_event_is_not_filtered_away_by_harness(self):
        self.server.event_body = b'{"Action":"start"}\n'
        self.assertEqual(self.case.operation()["bounded_events"], "false")

    def test_missing_socket_path_cannot_be_preexisting(self):
        (self.root / "f01-missing.sock").touch()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.case.operation()

    def test_unexpected_missing_backend_response_is_not_failure_success(self):
        with patch("fault_probe.request", return_value=(200, b"{}")):
            self.assertEqual(self.case.operation()["missing_backend_error"], "false")

    def test_permission_error_is_not_absence(self):
        with patch("fault_probe.request", side_effect=PermissionError("private")), self.assertRaises(PermissionError):
            self.case.operation()

    def test_parallel_transport_failure_joins_all_requests_before_return(self):
        with patch.object(self.fixture, "call", side_effect=TimeoutError("bounded")) as call:
            with self.assertRaises(TimeoutError):
                concurrent_requests(self.fixture, "POST", "/unused")
            self.assertEqual(call.call_count, 4)

    def test_repeated_operation_refused_without_repeating_races(self):
        self.case.operation()
        count = len(self.server.routes)
        with self.assertRaisesRegex(ValueError, "already attempted"):
            self.case.operation()
        self.assertEqual(len(self.server.routes), count)
        self.case.cleanup()

    def test_empty_cleanup_and_changed_owner(self):
        self.assertEqual(self.case.cleanup()["status"], "passed")
        self.case.operation()
        other = FaultFixture(self.socket, "f" * 64, self.server.image, "1.54", self.journal)
        with self.assertRaisesRegex(ValueError, "another case"):
            other.cleanup()
        self.case.cleanup()

    def test_invalid_journal_role_and_cross_role_records(self):
        with self.assertRaisesRegex(ValueError, "role"):
            ScopedJournal(self.journal, "foreign")
        ScopedJournal(self.journal, "signal").put("one", b"signal")
        ScopedJournal(self.journal, "remove").put("two", b"remove")
        self.assertEqual(ScopedJournal(self.journal, "signal").records(), {"one": b"signal"})

    def test_second_cleanup_failure_cannot_seal_composite_closure(self):
        self.case.operation()
        with patch.object(self.fixture, "cleanup", side_effect=ValueError("uncertain")), self.assertRaises(ValueError):
            self.case.cleanup()
        self.assertNotIn("fault-removed.json", self.journal.records())


if __name__ == "__main__":
    unittest.main()
