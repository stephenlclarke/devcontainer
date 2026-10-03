# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Regress missing suites, mixed sources, false passes and unattended failures."""

import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import layered_build as layers


class LayeredBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = {"commit": "a" * 40, "dirty": False, "files": {}, "tooling": {}}
        self.stage = layers.plan(layers.ROOT, ("stock",))[0]
        def retained(directory, events, _database):
            return {json.dumps(event["id"]["testResult"], sort_keys=True):
                    (directory / (event["id"]["testResult"]["label"].removeprefix("//:") + ".xml")).read_bytes()
                    for event in events if "testResult" in event}
        reader = patch.object(layers, "read_retained_test_xml", side_effect=retained)
        reader.start()
        self.addCleanup(reader.stop)

    def evidence(self, stage=None, directory=None) -> tuple[Path, list[dict]]:
        stage = stage or self.stage
        directory = directory or self.directory / "invocation"
        directory.mkdir()
        for name in ("inputs-before.json", "inputs-after.json"):
            layers.write(directory / name, self.source)
        layers.write(directory / "outcome.json", {"bazel_exit_code": 0, "validation_exit_code": 0})
        events = [
            {"unstructuredCommandLine": {"args": ["test", "--config=" + stage["profile"], stage["target"]]}},
            {"optionsParsed": {"cmdLine": ["--compilation_mode=dbg", "--config=" + stage["profile"],
                                           "--flaky_test_attempts=1"]}},
            {"finished": {"overallSuccess": True, "exitCode": {"code": 0}}},
        ]
        for label, minimum in stage["tests"].items():
            xml = directory / (label.removeprefix("//:") + ".xml")
            xml.write_text('<testsuite>' + ''.join(f'<testcase name="case{n}"/>' for n in range(minimum))
                           + '</testsuite>')
            events.extend([
                {"testSummary": {"overallStatus": "PASSED", "totalRunCount": 1},
                 "id": {"testSummary": {"label": label}}},
                {"id": {"testResult": {"label": label}}, "testResult": {"testActionOutput": [
                    {"name": "test.xml", "uri": (Path('/Volumes/SSD/cf/bazel') / xml.name).as_uri()}]}},
            ])
        self.events(directory, events)
        return directory, events

    def xml_reads(self, directory):
        """Virtualize only SSD file location; parse real fixture XML with validate()."""
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path.parent == Path('/Volumes/SSD/cf/bazel'):
                path = directory / path.name
            return original(path, *args, **kwargs)
        return patch.object(Path, "read_text", read)

    @staticmethod
    def events(directory: Path, events: list[dict]) -> None:
        (directory / "events.json").write_text("\n".join(json.dumps(event) for event in events) + "\n")

    def test_missing_policy_suite_is_rejected_before_execution(self) -> None:
        policy = layers.load_policy(layers.ROOT / "Tools/bazel/evidence-policy.json", "stock")
        policy["tests"]["//:AdditionalTests"] = 1
        with patch.object(layers, "load_policy", return_value=policy):
            with self.assertRaisesRegex(ValueError, "union"):
                layers.plan(layers.ROOT, ("stock",))
        for profiles in ((), ("stock", "stock"), ("unknown",)):
            with self.assertRaises(ValueError):
                layers.plan(layers.ROOT, profiles)

    def test_exact_profile_source_and_summary_are_required(self) -> None:
        directory, original = self.evidence()
        with patch.object(layers, "validate", return_value={"test_cases": {"model": 11}}) as validate:
            result = layers.admit(directory, self.stage, self.source)
            self.assertEqual(result["test_cases"], {"model": 11})
            validate.assert_called_once()
            self.assertEqual(validate.call_args.args, (original,))
            self.assertEqual(validate.call_args.kwargs["expected"], self.stage["tests"])
            self.assertEqual(len(validate.call_args.kwargs["retained_xml"]), len(self.stage["tests"]))
        for extra in ("--config=enhanced", "--config=prebuilt-argument-parser"):
            events = copy.deepcopy(original)
            events[0]["unstructuredCommandLine"]["args"].append(extra)
            self.events(directory, events)
            with self.assertRaisesRegex(ValueError, "source mode"):
                layers.admit(directory, self.stage, self.source)
        self.events(directory, original + [original[-2]])
        with self.assertRaisesRegex(ValueError, "single-attempt"):
            layers.admit(directory, self.stage, self.source)
        self.events(directory, original)
        layers.write(directory / "inputs-after.json", {**self.source, "commit": "b" * 40})
        with self.assertRaisesRegex(ValueError, "snapshot"):
            layers.admit(directory, self.stage, self.source)

    def test_successful_process_cannot_hide_failed_validation(self) -> None:
        directory, events = self.evidence()
        layers.write(directory / "outcome.json", {"bazel_exit_code": 0, "validation_exit_code": 2})
        with self.assertRaisesRegex(ValueError, "validation"):
            layers.admit(directory, self.stage, self.source)
        layers.write(directory / "outcome.json", {"bazel_exit_code": 0, "validation_exit_code": 0})
        events[2]["finished"]["overallSuccess"] = False
        self.events(directory, events)
        with self.assertRaisesRegex(ValueError, "result changed"):
                layers.admit(directory, self.stage, self.source)

    def test_real_xml_positive_and_failed_skipped_or_missing_cases_reject(self) -> None:
        directory, _ = self.evidence()
        xml = directory / "DevContainerModelTests.xml"
        original = xml.read_text()
        with self.xml_reads(directory):
            result = layers.admit(directory, self.stage, self.source)
            self.assertEqual(result["test_cases"], self.stage["tests"])
            self.assertFalse(result["all_tests_cached"])
            for content in ('<testsuite/>', original.replace('/>', '><skipped/></testcase>', 1),
                            original.replace('/>', '><failure/></testcase>', 1)):
                xml.write_text(content)
                with self.assertRaisesRegex(ValueError, "test cases"):
                    layers.admit(directory, self.stage, self.source)

    def test_filters_overrides_and_non_debug_modes_reject(self) -> None:
        directory, original = self.evidence()
        for argument in ("--test_filter=selected", "--test_arg=--skip", "--runs_per_test=2",
                         "--flaky_test_attempts=2", "--override_repository=dep=/tmp/other",
                         "--test_env=FILTER=one", "--@build_bazel_rules_swift//swift:copt=-O",
                         "--compilation_mode=opt", "//:AnotherTest"):
            events = copy.deepcopy(original)
            events[0]["unstructuredCommandLine"]["args"].append(argument)
            self.events(directory, events)
            with self.subTest(argument=argument), self.assertRaises(ValueError):
                layers.admit(directory, self.stage, self.source)
        for effective in (["--compilation_mode=opt", "--config=stock"],
                          ["--config=stock"],
                          ["--compilation_mode=dbg", "--config=stock", "--test_filter=one"]):
            events = copy.deepcopy(original)
            events[1]["optionsParsed"]["cmdLine"] = effective
            self.events(directory, events)
            with self.assertRaises(ValueError):
                layers.admit(directory, self.stage, self.source)

    def test_full_success_sequence_retains_all_eleven_suites_per_profile(self) -> None:
        output = self.directory / "success"
        stages = layers.plan(layers.ROOT, ("stock", "enhanced"))
        calls, selected = [], []

        def execute(command, _root, directory, _timeout):
            stage = stages[len(calls)]
            self.assertEqual(command, stage["command"])
            calls.append(command)
            invocation, _ = self.evidence(stage, directory / "invocation")
            selected[:] = [invocation]
            return {"exitCode": 0, "elapsedNS": 10}

        def admit(directory, stage, source):
            with self.xml_reads(directory):
                return original_admit(directory, stage, source)

        original_admit = layers.admit
        handlers = {number: signal.getsignal(number) for number in layers.CANCELLATION_SIGNALS}
        with patch.object(layers, "snapshot", return_value=self.source), \
                patch.object(layers, "execute", side_effect=execute), \
                patch.object(layers, "invocation_from_log", side_effect=lambda _: selected[0]), \
                patch.object(layers, "admit", side_effect=admit):
            result = layers.run(layers.ROOT, output, ("stock", "enhanced"), 60)
        self.assertEqual(len(calls), 12)
        self.assertEqual(result["status"], "passed")
        for profile in ("stock", "enhanced"):
            counts = {label: count for row in result["stages"] if row["profile"] == profile
                      for label, count in row["admission"]["test_cases"].items()}
            self.assertEqual(len(counts), 11)
        self.assertFalse(result["coverageQualified"])
        self.assertFalse(result["releaseQualified"])
        self.assertEqual(handlers, {number: signal.getsignal(number) for number in handlers})

    def test_plan_and_evidence_paths_do_not_run_commands(self) -> None:
        with patch.object(layers, "execute", side_effect=AssertionError("must not execute")):
            with patch.object(sys, "argv", ["layered_build.py", "--plan", "--profile", "stock"]), \
                    patch("builtins.print") as printed:
                layers.main()
            self.assertEqual(len(json.loads(printed.call_args.args[0])), 6)
            for output in (Path("relative"), layers.ROOT / "forbidden-evidence", self.directory):
                with self.assertRaisesRegex(ValueError, "fresh"):
                    layers.run(layers.ROOT, output, ("stock",), 60)

    def test_failed_stage_is_retained_without_later_execution(self) -> None:
        output = self.directory / "failed"
        with patch.object(layers, "snapshot", return_value=self.source), \
                patch.object(layers, "execute", return_value={"exitCode": 7, "elapsedNS": 10}) as execute:
            with self.assertRaisesRegex(ValueError, "command failed"):
                layers.run(layers.ROOT, output, ("stock",), 60)
        receipt = json.loads((output / "layers.json").read_text())
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["stages"][0]["status"], "failed")
        self.assertFalse(receipt["releaseQualified"])
        with self.assertRaisesRegex(ValueError, "fresh"):
            layers.run(layers.ROOT, output, ("stock",), 60)

    def test_source_change_between_layers_prevents_the_next_command(self) -> None:
        output = self.directory / "changed"
        changed = {**self.source, "commit": "b" * 40}
        # Initial, first precheck, first postcheck, then second precheck.
        with patch.object(layers, "snapshot", side_effect=[self.source] * 3 + [changed]), \
                patch.object(layers, "execute", return_value={"exitCode": 0, "elapsedNS": 10}) as execute, \
                patch.object(layers, "invocation_from_log", return_value=self.directory), \
                patch.object(layers, "admit", return_value={"test_cases": {}}):
            with self.assertRaisesRegex(ValueError, "between layers"):
                layers.run(layers.ROOT, output, ("stock",), 60)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(json.loads((output / "layers.json").read_text())["status"], "failed")

    def test_dirty_source_requires_explicit_development_scope(self) -> None:
        with patch.object(layers, "snapshot", return_value={**self.source, "dirty": True}):
            with self.assertRaisesRegex(ValueError, "clean checkpoint"):
                layers.run(layers.ROOT, self.directory / "dirty", ("stock",), 60)
        self.assertFalse((self.directory / "dirty").exists())

    def test_native_child_failure_and_timeout_preserve_logs(self) -> None:
        failed = self.directory / "child-failed"
        failed.mkdir()
        result = layers.execute([sys.executable, "-c", "print('retained'); raise SystemExit(3)"],
                                layers.ROOT, failed, 5)
        self.assertEqual(result["exitCode"], 3)
        self.assertIn("retained", (failed / "stdout.log").read_text())
        timed = self.directory / "child-timed"
        timed.mkdir()
        with self.assertRaises(subprocess.TimeoutExpired):
            layers.execute([sys.executable, "-c", "import time; time.sleep(30)"],
                           layers.ROOT, timed, 0.05)
        self.assertTrue((timed / "stderr.log").is_file())

    @staticmethod
    def wait_for(path, process, timeout=5):
        end = time.monotonic() + timeout
        while not path.exists():
            if process.poll() is not None or time.monotonic() >= end:
                raise AssertionError("test process did not reach " + str(path))
            time.sleep(0.01)

    def test_controller_signals_seal_failure_and_cleanup_owned_session(self) -> None:
        # The grandchild changes process group and ignores TERM. Killing only
        # the launcher leader/group must not make this regression pass.
        for number in layers.CANCELLATION_SIGNALS:
            with self.subTest(signal=number):
                case = self.directory / str(number)
                case.mkdir()
                child = "\n".join([
                    "import os,signal,time,pathlib,json",
                    "os.setpgrp()",
                    "signal.signal(signal.SIGTERM,signal.SIG_IGN)",
                    f"pathlib.Path({str(case / 'ready')!r}).write_text(json.dumps({{'pid':os.getpid(),'sid':os.getsid(0),'mask':[int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK,[])]}}))",
                    "time.sleep(30)",
                ])
                leader = "\n".join([
                    "import os,pathlib,subprocess,sys,time",
                    f"pathlib.Path({str(case / 'leader')!r}).write_text(str(os.getpid()))",
                    f"subprocess.Popen([sys.executable,'-c',{child!r}])",
                    "time.sleep(30)",
                ])
                script = "\n".join([
                    "import json,pathlib,sys,time",
                    f"sys.path.insert(0,{str(layers.ROOT / 'Tools/bazel')!r})",
                    "import layered_build as m",
                    f"m.snapshot=lambda _: {self.source!r}",
                    f"m.plan=lambda *_: [{{'name':'one','command':[sys.executable,'-c',{leader!r}]}}]",
                    "original=m.terminate_session",
                    "def cleanup(process):",
                    f" pathlib.Path({str(case / 'cleanup')!r}).write_text('ready')",
                    " end=time.monotonic()+5",
                    f" while not pathlib.Path({str(case / 'continue')!r}).exists() and time.monotonic()<end: time.sleep(.01)",
                    " original(process)",
                    "m.terminate_session=cleanup",
                    f"m.run(m.ROOT,pathlib.Path({str(case / 'output')!r}),('stock',),60)",
                ])
                with (case / "controller.log").open("wb") as log:
                    process = subprocess.Popen([sys.executable, "-c", script], stdout=log, stderr=log,
                                               start_new_session=True)
                    try:
                        self.wait_for(case / "ready", process)
                        member = json.loads((case / "ready").read_text())
                        self.assertFalse(set(member["mask"]) & set(layers.CANCELLATION_SIGNALS))
                        process.send_signal(number)
                        self.wait_for(case / "cleanup", process)
                        for repeated in layers.CANCELLATION_SIGNALS:
                            process.send_signal(repeated)
                        (case / "continue").write_text("continue")
                        self.assertNotEqual(process.wait(timeout=15), 0)
                        receipt = json.loads((case / "output/layers.json").read_text())
                        self.assertEqual(receipt["status"], "failed")
                        self.assertEqual(receipt["stages"][0]["status"], "failed")
                        self.assertIn(signal.Signals(number).name, receipt["failure"])
                        self.assertFalse(layers.session_members(member["sid"]))
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=5)
                        if (case / "leader").exists():
                            identifier = int((case / "leader").read_text())
                            for pid in layers.session_members(identifier):
                                try:
                                    if os.getsid(pid) == identifier:
                                        os.kill(pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass

    def test_successful_leader_with_surviving_descendant_is_failed_and_cleaned(self) -> None:
        output = self.directory / "descendant"
        output.mkdir()
        ready = output / "ready"
        child = ("import os,signal,time,pathlib; os.setpgrp(); "
                 "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                 f"pathlib.Path({str(ready)!r}).write_text(str(os.getsid(0))); time.sleep(30)")
        leader = ("import pathlib,subprocess,sys,time; "
                  f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
                  f"p=pathlib.Path({str(ready)!r})\n"
                  "while not p.exists(): time.sleep(.01)\n")
        with self.assertRaisesRegex(RuntimeError, "descendants remained"):
            layers.execute([sys.executable, "-c", leader], layers.ROOT, output, 5)
        self.assertFalse(layers.session_members(int(ready.read_text())))

    def test_cancellation_pending_during_spawn_keeps_child_handle_for_cleanup(self) -> None:
        output = self.directory / "spawn"
        output.mkdir()
        actual = subprocess.Popen
        owned = []

        def spawn(*args, **kwargs):
            process = actual(*args, **kwargs)
            if args[0][0] != sys.executable:
                return process
            owned.append(process)
            os.kill(os.getpid(), signal.SIGTERM)
            return process

        with layers.cancellation_handlers(), patch.object(layers.subprocess, "Popen", side_effect=spawn):
            with self.assertRaises(layers.Cancelled):
                layers.execute([sys.executable, "-c", "import time; time.sleep(30)"],
                               layers.ROOT, output, 5)
        self.assertEqual(len(owned), 1)
        self.assertIsNotNone(owned[0].returncode)
        self.assertFalse(layers.session_members(owned[0].pid))


if __name__ == "__main__":
    unittest.main()
