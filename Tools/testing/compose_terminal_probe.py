"""Actual Compose CLI terminal-size convergence with preserved startup evidence."""

import errno
import fcntl
import os
import re
import signal
import struct
import termios
import time
import tty

from case_evidence import canonical
from compose_foreground_probe import ComposeForegroundFixture, ComposeSignalFixture, PROCESS, STDOUT, STDERR
from exec_probe import remaining
from foreground_probe import ForegroundFixture


class ComposeTerminalSizeFixture(ComposeForegroundFixture):
    """Preserve first-query evidence, then require bounded CLI resize convergence."""

    expected_tty = True
    convergence_timeout = 5
    command = ("sh", "-c", "stty size; status=$?; stty -echo -onlcr; "
               "printf 'initial-status:%s\\n' \"$status\"; "
               "printf 'compose-stdout\\n'; printf 'compose-stderr\\n' >&2; "
               "while IFS= read -r line; do case \"$line\" in "
               "size) value=$(stty size 2>&1); status=$?; printf 'size:%s:%s\\n' \"$status\" \"$value\";; "
               "quit) exit 17;; *) printf 'seen:%s\\n' \"$line\";; esac; done; exit 19")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.intent["command"] = list(self.command)
        self.intent["composeTerminal"] = {"contractVersion": 2, "rows": 37, "columns": 113,
                                          "resizeRows": 53, "resizeColumns": 121,
                                          "queryBeforeInput": True, "convergenceSeconds": self.convergence_timeout}
        self.master = None
        self.capture = None
        self.terminal_eof = False
        self.expected_output = b""

    def drain(self):
        """Bounded nonblocking reads retain raw PTY bytes, including CRLF."""
        if self.master is None or self.terminal_eof:
            return
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            # A PTY with no slave writers reports EOF as EIO on some hosts.
            data = b""
        if not data:
            self.terminal_eof = True
            return
        # Retain the exclusively created descriptor, not a child-writable path.
        # One byte beyond the diagnostic limit records truncation; draining
        # excess must never prevent owned-process/resource cleanup.
        available = max(0, 1024**2 + 1 - self.capture.tell())
        self.capture.write(data[:available])

    def snapshot(self, path):
        if path == self.output:
            self.drain()
        return super().snapshot(path)

    def ready(self, end):
        while True:
            output = self.snapshot(self.output)
            if output.endswith(STDOUT + STDERR):
                # Do not invent a pre-entrypoint size guarantee. Both valid
                # dimensions and the pinned guest's unavailable-size diagnostic
                # remain byte-exact first-query evidence, not a resized result.
                first = output[:-(len(STDOUT) + len(STDERR))]
                # The pinned guest's unavailable-size diagnostic can carry a
                # zero exit status; only a later numeric size proves readiness.
                if re.fullmatch(rb"(?:[0-9]+ [0-9]+\r\ninitial-status:0\n|stty: standard input\r\ninitial-status:[01]\n)", first) is None:
                    raise ValueError("Unexpected initial terminal query output")
                self.ready_output = output
                super().ready(end)
                self.expected_output = output
                self.journal.put("compose-terminal-initial.json", canonical({"rawOutputHex": first.hex()}))
                return
            if len(output) > 4096 or self.child.process.poll() is not None:
                raise ValueError("Compose CLI exited or emitted unexpected foreground stdout")
            time.sleep(min(remaining(end), 0.01))

    def send_input(self, payload):
        if os.write(self.master, payload) != len(payload):
            raise ValueError("Compose terminal input was incomplete")

    def size_sample(self, end):
        remaining(end)
        self.send_input(b"size\n")
        while True:
            remaining(end)
            output = self.snapshot(self.output)
            if not output.startswith(self.expected_output):
                raise ValueError("Compose terminal changed prior guest bytes")
            tail = output[len(self.expected_output):]
            if b"\n" in tail:
                if re.fullmatch(rb"size:(?:0:[0-9]+ [0-9]+|[01]:stty: standard input)\n", tail) is None:
                    raise ValueError("Compose terminal emitted unexpected size sample")
                remaining(end)
                self.expected_output = output
                return tail
            if len(tail) > 4096 or self.child.process.poll() is not None or self.terminal_eof:
                raise ValueError("Compose terminal ended before a complete size sample")
            time.sleep(min(remaining(end), 0.01))

    def converge(self, rows, columns, phase, overall_end):
        started = time.monotonic_ns()
        end = min(overall_end, time.monotonic() + self.convergence_timeout)
        expected = f"size:0:{rows} {columns}\n".encode()
        samples = []
        try:
            while True:
                remaining(end)
                sample = self.size_sample(end)
                samples.append({"rawOutputHex": sample.hex(), "elapsedNS": time.monotonic_ns() - started})
                if sample == expected:
                    return
                time.sleep(min(remaining(end), 0.025))
        finally:
            self.journal.put("compose-terminal-" + phase + "-samples.json", canonical({"samples": samples,
                             "durationNS": time.monotonic_ns() - started, "rows": rows, "columns": columns}))

    def resize_host(self):
        self.runtime.verify()
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", 53, 121, 0, 0))
        # Signal only the revalidated, unreaped CLI, never its guest or group.
        ComposeSignalFixture.send_signal(self, "SIGWINCH", signal.SIGWINCH)

    def operation(self):
        arguments = self.prepare()
        started, end = time.monotonic_ns(), time.monotonic() + 45
        self.runtime.verify()
        self.capture = self.output.open("xb", buffering=0)
        self.master, slave = os.openpty()
        try:
            # Keep only the guest's line discipline. The host must neither echo
            # input nor apply a second CRLF conversion to guest output.
            tty.setraw(slave)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 37, 113, 0, 0))
            os.set_blocking(self.master, False)
            with self.errors.open("xb") as errors:
                self.command_attempted = True
                self.child.start(arguments, self.root, slave, errors=errors, stdin=slave,
                                 runtime_socket=self.socket, provider_install=self.provider_install)
            self.journal.put(PROCESS + "-process.json", canonical(self.child.identity()))
        finally:
            os.close(slave)
        self.ready(end)
        self.converge(37, 113, "inherited", end)
        self.resize_host()
        self.converge(53, 121, "resized", end)
        token = ("compose-terminal-" + self.owner[:16]).encode()
        self.send_input(token + b"\nquit\n")
        while self.child.process.poll() is None or not self.terminal_eof:
            self.snapshot(self.output)
            time.sleep(min(remaining(end), 0.01))
        code = self.child.process.wait(timeout=remaining(end))
        self.journal.put(PROCESS + "-exit.json", canonical({"code": code,
                         "durationNS": time.monotonic_ns() - started}))
        if code != 17:
            raise ValueError("Compose terminal lost the guest exit status")
        if self.snapshot(self.output) != self.expected_output + b"seen:" + token + b"\n":
            raise ValueError("Compose terminal changed or duplicated guest bytes")
        if any(marker in self.snapshot(self.errors) for marker in (b"compose-stdout", b"compose-stderr", token)):
            raise ValueError("Compose terminal split merged guest output")
        ForegroundFixture.require_auto_removed(self)
        self.runtime.verify()
        return {key: "true" for key in ("inherited_size", "host_resize", "tty_selected", "stdin_roundtrip", "merged_streams",
                                         "exact_exit", "auto_remove")}

    def cleanup(self):
        # Keep the master alive until the owned CLI stops; closing it earlier
        # would inject a hangup instead of testing/recovering the real command.
        self.child.stop()
        try:
            while self.master is not None and not self.terminal_eof:
                before = self.capture.tell()
                self.drain()
                if self.capture.tell() == before:
                    break
        finally:
            if self.master is not None:
                os.close(self.master)
                self.master = None
            if self.capture is not None:
                self.capture.close()
                self.capture = None
        return super().cleanup()
