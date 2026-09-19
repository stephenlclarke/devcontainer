"""D06 published loopback HTTP, forwarded metadata and owned port collisions."""

from http.client import HTTPResponse
from io import BytesIO
import json
from pathlib import Path
import socket
import time
from types import SimpleNamespace

from case_evidence import canonical, digest
from devcontainer_reference import DevcontainerReference
from guest_fixture import GuestFixture


FIXTURE = "D06-ports"
IMAGE = "python:3.13-alpine@sha256:399babc8b49529dabfd9c922f2b5eea81d611e4512e3ed250d75bd2e7683f4b0"
BINDINGS = {"8123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49277"}]}
FORWARD = {"forwardPorts": [8123], "portsAttributes": {"8123": {"label": "Parity HTTP"}}}
HOST_PORT = 49277
HTTP_BODY = b"devcontainer-port\n"


def fixture_inputs(repository: Path) -> dict:
    root = repository / "Tests/Parity/fixtures" / FIXTURE
    result = {key: (root / path).read_text() for key, path in (
        ("configuration", ".devcontainer/devcontainer.json"), ("probe", "probe.sh"))}
    config = json.loads(result["configuration"])
    if (config.get("image") != IMAGE or config.get("appPort") != ["127.0.0.1:49277:8123"] or
            any(config.get(key) != value for key, value in FORWARD.items()) or
            config.get("remoteUser") != "root" or config.get("overrideCommand") is not True):
        raise ValueError("D06 configuration or image pin changed")
    return result


class CollisionJournal:
    """Isolate the second container's ordinary ownership records."""

    def __init__(self, journal):
        self.journal = journal

    def records(self):
        return {name.removeprefix("d06-collision-"): data for name, data in self.journal.records().items()
                if name.startswith("d06-collision-")}

    def put(self, name, data):
        self.journal.put("d06-collision-" + name, data)


class PortCollision(GuestFixture):
    def __init__(self, client, image: str):
        owner = digest(canonical({"parent": client.owner, "purpose": "port-collision"}))
        super().__init__(client.socket, owner, image, client.version, CollisionJournal(client.journal),
                         command=("sleep", "30"), network="bridge", observe=client.observe)
        self.id_pattern = client.id_pattern
        self.intent["portBindings"] = BINDINGS

    def creation_body(self, body):
        return {**body, "ExposedPorts": {"8123/tcp": {}},
                "HostConfig": {**body["HostConfig"], "PortBindings": BINDINGS}}

    def owned(self, value):
        identifier = super().owned(value)
        if value.get("HostConfig", {}).get("PortBindings") != BINDINGS:
            raise ValueError("D06 collision port ownership changed")
        return identifier

    def reject(self):
        actual = self.create()
        if actual.get("State", {}).get("Status") != "created":
            raise ValueError("D06 collision must fail at start, not creation")
        self.journal.put("start-intent.json", canonical({"id": self.identifier}))
        status, payload = self.call("POST", f"/containers/{self.identifier}/start", timeout=30)
        result = json.loads(payload)
        self.journal.put("start-result.json", canonical({"status": status, "result": result}))
        message = result.get("message") if isinstance(result, dict) else None
        if (status != 500 or not isinstance(message, str) or
                not any(text in message.lower() for text in ("address already in use", "port is already allocated"))):
            raise ValueError("D06 collision did not produce the expected port-allocation rejection")
        actual = self.inspect(self.identifier)
        if actual is None or self.owned(actual) != self.identifier or actual.get("State", {}).get("Running") is not False:
            raise ValueError("D06 rejected collision container is running or changed")


def require_free_port():
    # Do not adopt or stop an unrelated listener on the fixed fixture port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", HOST_PORT))


def http_response(payload: bytes):
    # Parse only an already bounded, complete connection-close response. Never
    # let HTTP's buffered readline hide a peer that trickles header/body bytes.
    response = HTTPResponse(SimpleNamespace(makefile=lambda *_: BytesIO(payload)))
    response.begin()
    lengths = response.headers.get_all("Content-Length", [])
    if (response.status != 200 or lengths != [str(len(HTTP_BODY))] or payload.partition(b"\r\n\r\n")[2] != HTTP_BODY or
            response.headers.get_all("Transfer-Encoding", []) or response.read() != HTTP_BODY):
        raise ValueError("D06 published endpoint returned unexpected HTTP content or framing")


def host_http(timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                connection.settimeout(min(1, max(0.001, end - time.monotonic())))
                connection.connect(("127.0.0.1", HOST_PORT))
                connection.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                payload = bytearray()
                while True:
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("D06 HTTP response exceeded its deadline")
                    connection.settimeout(min(1, remaining))
                    chunk = connection.recv(8193 - len(payload))
                    if not chunk:
                        break
                    payload.extend(chunk)
                    if len(payload) > 8192:
                        raise ValueError("D06 HTTP response exceeded its byte bound")
                if time.monotonic() >= end:
                    raise TimeoutError("D06 HTTP response exceeded its deadline")
                http_response(bytes(payload))
            return
        except (ConnectionRefusedError, ConnectionResetError, TimeoutError, socket.timeout):
            time.sleep(max(0, min(0.05, end - time.monotonic())))
    raise TimeoutError("D06 published HTTP endpoint did not become ready")


class DevcontainerPortsReference(DevcontainerReference):
    fixture = FIXTURE
    reference_image = IMAGE
    keys = {"forward_metadata", "inside_connectivity"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.collision = None

    def prepare(self):
        require_free_port()
        super().prepare()

    def arguments(self, command):
        arguments = super().arguments(command)
        return arguments + (["--include-configuration"] if command == "up" else [])

    def up_identity(self, output):
        identifier = super().up_identity(output)
        config = json.loads(output).get("configuration")
        if not isinstance(config, dict) or any(config.get(key) != value for key, value in FORWARD.items()):
            raise ValueError("D06 CLI forward metadata differs from the original configuration")
        return identifier

    def owned(self, value):
        identifier = super().owned(value)
        if value.get("HostConfig", {}).get("PortBindings") != BINDINGS:
            raise ValueError("D06 published port binding changed")
        return identifier

    def execute(self):
        result = super().execute()
        host_http()
        actual = self.find()
        if actual is None or self.owned(actual) != self.identifier:
            raise ValueError("D06 primary container changed before collision test")
        self.journal.put("d06-collision-plan.json", canonical({"image": actual["Image"]}))
        self.collision = PortCollision(self, actual["Image"])
        self.collision.reject()
        host_http()
        return {**result, "host_connectivity": "true", "collision_rejected": "true"}

    def remove_owned(self):
        records = self.journal.records()
        plan = json.loads(records.get("d06-collision-plan.json", b"null"))
        if plan is not None:
            admitted = self.inputs["workload"]["image"]
            if not isinstance(plan, dict) or plan.get("image") not in {
                    admitted["manifest"], admitted["config"], IMAGE.split("@", 1)[1]}:
                raise ValueError("D06 collision image plan changed")
            self.collision = PortCollision(self, plan["image"])
            self.collision.cleanup()
        super().remove_owned()
