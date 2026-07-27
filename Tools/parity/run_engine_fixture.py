#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Exercise Docker Engine contracts and print normalized observations."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from docker_api import DockerAPI


class Probe:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier
        self.docker = os.environ.get("DEVCONTAINER_DOCKER_BIN") or shutil.which(
            "docker"
        )
        if not self.docker:
            raise RuntimeError("docker CLI is required")
        self.name = (
            "dcparity-"
            + identifier.lower().replace("_", "-")[:30]
            + f"-{os.getpid()}"
        )
        self.containers: list[str] = []
        self.images: list[str] = []
        self.networks: list[str] = []
        self.volumes: list[str] = []

    def command(
        self,
        *arguments: str,
        check: bool = True,
        input_data: bytes | None = None,
        timeout: int = 180,
    ) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            [self.docker, *arguments],
            input=input_data,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=os.environ,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"docker {' '.join(arguments)} exited {result.returncode}: "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        return result

    def emit(self, **values: object) -> None:
        for key in sorted(values):
            value = values[key]
            text = (
                "true"
                if value is True
                else "false"
                if value is False
                else str(value)
            )
            print(f"{key}={text}")

    def cleanup(self) -> None:
        for container in reversed(self.containers):
            self.command("rm", "-f", container, check=False, timeout=60)
        for network in reversed(self.networks):
            self.command("network", "rm", network, check=False, timeout=60)
        for volume in reversed(self.volumes):
            self.command("volume", "rm", "-f", volume, check=False, timeout=60)
        for image in reversed(self.images):
            self.command("image", "rm", "-f", image, check=False, timeout=120)

    def engine_negotiation(self) -> None:
        api = DockerAPI(self.docker)
        ping = api.request("GET", "/_ping")
        version = api.request("GET", "/version")
        minimum = str(version.json().get("MinAPIVersion", "1.44"))
        versioned = api.request("GET", f"/v{minimum}/_ping")
        head = api.request("HEAD", "/_ping")
        missing = api.request("GET", "/v1.24/devcontainer-missing")
        malformed = api.request(
            "POST",
            "/v1.24/containers/create?name=bad",
            body=b"{",
            headers={"Content-Type": "application/json"},
        )
        error_value = json.loads(missing.body)
        malformed_value = json.loads(malformed.body)
        self.emit(
            api_prefix=versioned.status == 200 and versioned.body == b"OK",
            error_envelope=missing.status == 404
            and isinstance(error_value.get("message"), str),
            head_ping=head.status == 200 and not head.body,
            malformed_request=malformed.status == 400
            and isinstance(malformed_value.get("message"), str),
            ping=ping.status == 200 and ping.body == b"OK",
        )

    def container_lifecycle(self) -> None:
        self.command("pull", "alpine:latest", timeout=300)
        name = self.name + "-container"
        created = self.command(
            "create",
            "--name",
            name,
            "--label",
            "devcontainer.parity=E02",
            "alpine:latest",
            "sh",
            "-c",
            "exit 7",
        ).stdout.decode().strip()
        self.containers.append(created)
        created_state = (
            self.command("inspect", "-f", "{{.State.Status}}", created)
            .stdout.decode()
            .strip()
        )
        self.command("start", created)
        wait_code = self.command("wait", created).stdout.decode().strip()
        inspect_code = (
            self.command("inspect", "-f", "{{.State.ExitCode}}", created)
            .stdout.decode()
            .strip()
        )
        self.command("rm", created)
        self.containers.remove(created)
        repeated = self.command("rm", created, check=False)
        self.emit(
            create_state=created_state,
            exit_status=inspect_code,
            idempotent_cleanup=repeated.returncode != 0,
            wait_status=wait_code,
        )

    def exec_streams(self) -> None:
        self.command("pull", "alpine:latest", timeout=300)
        container = self.command(
            "run", "-d", "--name", self.name, "alpine:latest", "sleep", "300"
        ).stdout.decode().strip()
        self.containers.append(container)
        environment = self.command(
            "exec",
            "-e",
            "PARITY_VALUE=present",
            "-w",
            "/tmp",
            "-u",
            "0:0",
            container,
            "sh",
            "-c",
            'printf "%s|%s|%s" "$PARITY_VALUE" "$PWD" "$(id -u):$(id -g)"',
        )
        streams = self.command(
            "exec",
            container,
            "sh",
            "-c",
            "printf stdout-value; printf stderr-value >&2; exit 7",
            check=False,
        )
        payload = bytes(range(256)) * 16_384
        binary = self.command(
            "exec",
            "-i",
            container,
            "cat",
            input_data=payload,
            timeout=300,
        )
        tty = self.command(
            "exec",
            "-t",
            container,
            "sh",
            "-c",
            "test -t 1 && printf tty-value",
        )
        self.emit(
            binary_duplex=hashlib.sha256(binary.stdout).digest()
            == hashlib.sha256(payload).digest(),
            environment=environment.stdout.decode() == "present|/tmp|0:0",
            exact_exit=streams.returncode == 7,
            stderr=streams.stderr.decode() == "stderr-value",
            stdout=streams.stdout.decode() == "stdout-value",
            tty="tty-value" in tty.stdout.decode(errors="replace"),
        )

    def image_build(self) -> None:
        tag = self.name + ":latest"
        self.images.append(tag)
        with tempfile.TemporaryDirectory(prefix="devcontainer-e04-") as directory:
            root = Path(directory)
            (root / "Dockerfile").write_text(
                "FROM alpine:latest\n"
                "ARG PARITY_VALUE\n"
                'RUN test "$PARITY_VALUE" = expected\n'
                'LABEL devcontainer.parity="true"\n'
                'CMD ["true"]\n',
                encoding="utf-8",
            )
            built = self.command(
                "build",
                "--progress",
                "plain",
                "--build-arg",
                "PARITY_VALUE=expected",
                "--tag",
                tag,
                "--load",
                str(root),
                timeout=900,
            )
            label = (
                self.command(
                    "image",
                    "inspect",
                    "-f",
                    '{{index .Config.Labels "devcontainer.parity"}}',
                    tag,
                )
                .stdout.decode()
                .strip()
            )
            (root / "Dockerfile").write_text(
                "FROM alpine:latest\nRUN false\n", encoding="utf-8"
            )
            failed = self.command(
                "build",
                "--progress",
                "plain",
                "--tag",
                tag + "-failed",
                str(root),
                check=False,
                timeout=900,
            )
        self.emit(
            build_progress=bool(built.stdout or built.stderr),
            failed_build=failed.returncode != 0,
            inspect_label=label == "true",
        )

    def archive_copy(self) -> None:
        self.command("pull", "alpine:latest", timeout=300)
        container = self.command(
            "run", "-d", "--name", self.name, "alpine:latest", "sleep", "300"
        ).stdout.decode().strip()
        self.containers.append(container)
        with tempfile.TemporaryDirectory(prefix="devcontainer-e05-") as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            regular = source / "regular.txt"
            regular.write_text("archive-content\n", encoding="utf-8")
            regular.chmod(0o750)
            (source / "regular-link").symlink_to("regular.txt")
            long_name = "long-" + "x" * 110
            (source / long_name).write_text("long-name\n", encoding="utf-8")
            large = source / "large.bin"
            large.write_bytes(bytes(range(256)) * 4096)
            self.command("exec", container, "mkdir", "-p", "/archive")
            self.command("cp", str(source) + "/.", f"{container}:/archive")
            destination = root / "destination"
            self.command("cp", f"{container}:/archive", str(destination))
            copied = destination / "archive"
            if not copied.exists():
                copied = destination
            mode = stat.S_IMODE((copied / "regular.txt").lstat().st_mode)
            self.emit(
                content=(copied / "regular.txt").read_text(encoding="utf-8")
                == "archive-content\n",
                large_file=(copied / "large.bin").read_bytes()
                == large.read_bytes(),
                long_path=(copied / long_name).read_text(encoding="utf-8")
                == "long-name\n",
                mode=oct(mode),
                symlink=(copied / "regular-link").is_symlink()
                and os.readlink(copied / "regular-link") == "regular.txt",
            )

    def network_volume(self) -> None:
        self.command("pull", "alpine:latest", timeout=300)
        network = self.name + "-network"
        volume = self.name + "-volume"
        self.command("network", "create", network)
        self.networks.append(network)
        self.command("volume", "create", volume)
        self.volumes.append(volume)
        temporary_root = Path.cwd() / ".build" / "parity-host"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="devcontainer-e06-",
            dir=temporary_root,
        ) as directory:
            bind = Path(directory)
            (bind / "input.txt").write_text("read-only\n", encoding="utf-8")
            app = self.command(
                "run",
                "-d",
                "--name",
                self.name + "-app",
                "--network",
                network,
                "--network-alias",
                "app",
                "--mount",
                f"type=volume,source={volume},target=/data",
                "--mount",
                f"type=bind,source={bind},target=/input,readonly",
                "--mount",
                "type=tmpfs,target=/scratch",
                "alpine:latest",
                "sleep",
                "300",
            ).stdout.decode().strip()
            self.containers.append(app)
            peer = self.command(
                "run",
                "-d",
                "--name",
                self.name + "-peer",
                "--network",
                network,
                "alpine:latest",
                "sleep",
                "300",
            ).stdout.decode().strip()
            self.containers.append(peer)
            dns = self.command("exec", peer, "ping", "-c", "1", "app")
            self.command("exec", app, "sh", "-c", "printf volume-data >/data/value")
            persisted = self.command(
                "run",
                "--rm",
                "--mount",
                f"type=volume,source={volume},target=/data",
                "alpine:latest",
                "cat",
                "/data/value",
            )
            readonly = self.command(
                "exec",
                app,
                "sh",
                "-c",
                "printf changed >/input/input.txt",
                check=False,
            )
            tmpfs = self.command(
                "exec",
                app,
                "sh",
                "-c",
                "printf scratch >/scratch/value && cat /scratch/value",
            )
            self.emit(
                bind_read_only=readonly.returncode != 0,
                network_dns=dns.returncode == 0,
                network_inspect=self.command(
                    "network", "inspect", network
                ).returncode
                == 0,
                tmpfs=tmpfs.stdout.decode() == "scratch",
                volume_inspect=self.command("volume", "inspect", volume).returncode
                == 0,
                volume_persistence=persisted.stdout.decode() == "volume-data",
            )

    def fault_recovery(self) -> None:
        self.command("pull", "alpine:latest", timeout=300)
        concurrent_container = self.command(
            "create",
            "--name",
            self.name + "-concurrent",
            "alpine:latest",
            "sh",
            "-c",
            "trap 'exit 42' TERM; while :; do sleep 1; done",
        ).stdout.decode().strip()
        self.containers.append(concurrent_container)

        def start() -> int:
            return self.command(
                "start",
                concurrent_container,
                check=False,
                timeout=60,
            ).returncode

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            start_results = list(executor.map(lambda _: start(), range(4)))

        self.command("kill", "--signal", "TERM", concurrent_container)
        signal_status = (
            self.command("wait", concurrent_container).stdout.decode().strip()
        )

        race_container = self.command(
            "create",
            "--name",
            self.name + "-remove",
            "alpine:latest",
            "true",
        ).stdout.decode().strip()
        self.containers.append(race_container)

        def remove() -> int:
            return self.command(
                "rm",
                "--force",
                race_container,
                check=False,
                timeout=60,
            ).returncode

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            remove_results = list(executor.map(lambda _: remove(), range(4)))
        absent = self.command("inspect", race_container, check=False).returncode != 0
        if absent:
            self.containers.remove(race_container)

        event_start = str(int(time.time()))
        event_end = str(int(time.time()) + 1)
        started = subprocess.run(
            [
                self.docker,
                "events",
                "--since",
                event_start,
                "--until",
                event_end,
                "--filter",
                "event=devcontainer-never",
            ],
            capture_output=True,
            check=False,
            timeout=10,
            env=os.environ,
        )
        missing_environment = dict(os.environ)
        missing_environment["DOCKER_HOST"] = (
            "unix:///tmp/devcontainer-parity-missing-backend.sock"
        )
        missing = subprocess.run(
            [self.docker, "version"],
            capture_output=True,
            check=False,
            timeout=10,
            env=missing_environment,
        )
        self.emit(
            bounded_events=started.returncode == 0,
            concurrent_start=all(code == 0 for code in start_results),
            missing_backend_error=missing.returncode != 0,
            remove_race=remove_results.count(0) >= 1 and absent,
            signal_exit=signal_status in {"42", "143"},
        )


METHODS: dict[str, Callable[[Probe], None]] = {
    "E01-engine-negotiation": Probe.engine_negotiation,
    "E02-container-lifecycle": Probe.container_lifecycle,
    "E03-exec-streams": Probe.exec_streams,
    "E04-image-build": Probe.image_build,
    "E05-archive-copy": Probe.archive_copy,
    "E06-network-volume": Probe.network_volume,
    "F01-fault-recovery": Probe.fault_recovery,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", choices=sorted(METHODS))
    arguments = parser.parse_args()
    probe = Probe(arguments.fixture)
    try:
        METHODS[arguments.fixture](probe)
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"probe error: {error}", file=sys.stderr)
        return 1
    finally:
        probe.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
