"""Real host PTY dimensions must reach the first guest terminal query."""

import errno
import fcntl
import os
import struct
import termios
import time
import tty

from case_evidence import canonical
from compose_foreground_probe import ComposeForegroundFixture, PROCESS
from exec_probe import remaining
from foreground_probe import ForegroundFixture


class ComposeTerminalSizeFixture(ComposeForegroundFixture):
    """No guest exec/resize request or readiness handshake precedes `stty size`."""

    expected_tty = True
    ready_output = b"37 113\r\ncompose-stdout\ncompose-stderr\n"
    command = ("sh", "-c", "stty size; stty -echo -onlcr; "
               "printf 'compose-stdout\\n'; printf 'compose-stderr\\n' >&2; "
               "IFS= read -r line || exit 19; printf 'seen:%s\\n' \"$line\"; exit 17")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.intent["command"] = list(self.command)
        self.intent["composeTerminal"] = {"rows": 37, "columns": 113, "queryBeforeInput": True}
        self.master = None
        self.capture = None
        self.terminal_eof = False

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
        self.journal.put("compose-terminal-initial.json", canonical({"rows": 37, "columns": 113,
                         "rawOutputHex": self.snapshot(self.output).hex()}))
        token = ("compose-terminal-" + self.owner[:16]).encode()
        if os.write(self.master, token + b"\n") != len(token) + 1:
            raise ValueError("Compose terminal input was incomplete")
        while self.child.process.poll() is None or not self.terminal_eof:
            self.snapshot(self.output)
            time.sleep(min(remaining(end), 0.01))
        code = self.child.process.wait(timeout=remaining(end))
        self.journal.put(PROCESS + "-exit.json", canonical({"code": code,
                         "durationNS": time.monotonic_ns() - started}))
        if code != 17:
            raise ValueError("Compose terminal lost the guest exit status")
        if self.snapshot(self.output) != self.ready_output + b"seen:" + token + b"\n":
            raise ValueError("Compose terminal changed or duplicated guest bytes")
        if any(marker in self.snapshot(self.errors) for marker in (b"compose-stdout", b"compose-stderr", token)):
            raise ValueError("Compose terminal split merged guest output")
        ForegroundFixture.require_auto_removed(self)
        self.runtime.verify()
        return {key: "true" for key in ("initial_size", "tty_selected", "stdin_roundtrip", "merged_streams",
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
