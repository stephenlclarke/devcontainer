# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Pure checks for generated, architecture-bound launcher identities."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("terminal_launcher_identity", Path(__file__).with_name("terminal_launcher_identity.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def elf(machine: int, interpreter: bool = False) -> bytes:
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", header, 18, machine)
    if not interpreter:
        struct.pack_into("<Q", header, 32, 64)
        struct.pack_into("<HH", header, 54, 56, 0)
        return bytes(header)
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<HH", header, 54, 56, 1)
    return bytes(header + struct.pack("<I", 3) + bytes(52))


class TerminalLauncherIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.arm64 = self.root / "arm64"
        self.amd64 = self.root / "amd64"
        self.arm64.write_bytes(elf(183))
        self.amd64.write_bytes(elf(62))

    def tearDown(self):
        self.temporary.cleanup()

    def test_emits_exact_architecture_hashes(self):
        source = MODULE.swift_source(self.arm64, self.amd64)
        self.assertIn(MODULE.hashlib.sha256(self.arm64.read_bytes()).hexdigest(), source)
        self.assertIn(MODULE.hashlib.sha256(self.amd64.read_bytes()).hexdigest(), source)
        self.assertIn('"arm64":', source)
        self.assertIn('"amd64":', source)

    def test_rejects_wrong_machine_and_non_elf(self):
        self.arm64.write_bytes(elf(62))
        with self.assertRaisesRegex(ValueError, "wrong ELF machine"):
            MODULE.inspect_elf(self.arm64, "arm64")
        self.arm64.write_bytes(b"not an ELF" + bytes(64))
        with self.assertRaisesRegex(ValueError, "not a little-endian ELF64"):
            MODULE.inspect_elf(self.arm64, "arm64")

    def test_rejects_dynamic_launcher(self):
        self.arm64.write_bytes(elf(183, interpreter=True))
        with self.assertRaisesRegex(ValueError, "dynamically linked"):
            MODULE.inspect_elf(self.arm64, "arm64")


if __name__ == "__main__":
    unittest.main()
