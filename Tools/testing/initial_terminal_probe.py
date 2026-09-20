"""E15 explicit Engine create-time dimensions, before guest input or resize."""

import time

from case_evidence import canonical
from exec_probe import remaining
from foreground_probe import ForegroundFixture, require_eof


FIXTURE = "E15-initial-terminal-size"
DIMENSIONS = (37, 113)  # Engine order: rows, columns.
OUTPUT = b"initial-size:37 113\n"
COMMAND = ("sh", "-c", "stty -echo -onlcr; printf 'initial-size:'; stty size; "
           'IFS= read -r line; [ "$line" = quit ] || exit 19; exit 17')


class InitialTerminalSizeFixture(ForegroundFixture):
    """Reuse the existing owned terminal, acknowledged wait and cleanup boundary."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, command=COMMAND, **kwargs)
        self.intent["foreground"]["initialConsoleSize"] = list(DIMENSIONS)

    def creation_body(self, body):
        result = super().creation_body(body)
        result["HostConfig"]["ConsoleSize"] = list(DIMENSIONS)
        return result

    def receive_initial_size(self, connection, initial, end):
        output = bytearray(initial)
        try:
            # Read one extra byte to distinguish a full prefix from overflow.
            while b"\n" not in output and len(output) < 65:
                connection.settimeout(remaining(end))
                chunk = connection.recv(65 - len(output))
                if not chunk:
                    raise ValueError("Initial terminal closed before its size observation")
                output.extend(chunk)
            if output != OUTPUT:
                raise ValueError("First guest terminal size differs from explicit create dimensions")
        finally:
            # Preserve mismatches/timeouts privately, never normalize raw output.
            self.journal.put("initial-terminal-output.log", bytes(output[:64]))
            self.journal.put("initial-terminal-capture.json", canonical({
                "observedBytes": len(output), "retainedBytes": min(len(output), 64),
                "truncated": len(output) > 64,
            }))

    def operation(self):
        created = self.create()
        if created.get("State", {}).get("Status") != "created":
            raise ValueError("Initial terminal process ran before subscriptions")
        if created.get("HostConfig", {}).get("ConsoleSize") != list(DIMENSIONS):
            raise ValueError("Inspection does not retain requested initial terminal dimensions")
        end = time.monotonic() + 30
        with self.exit_subscription(end) as (wait_connection, response):
            with self.attachment(end) as (connection, initial):
                self.start()
                self.receive_initial_size(connection, initial, end)
                # The first guest observation precedes all input and resize calls.
                connection.sendall(b"quit\n")
                require_eof(connection, end)
            self.require_exit(wait_connection, response, end)
        self.require_auto_removed()
        return {key: "true" for key in ("requested_size_inspected", "initial_size_before_input",
                                        "acknowledged_wait", "exact_exit", "auto_remove")}
