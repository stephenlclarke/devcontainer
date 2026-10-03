# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""A private runtime HOME must not redirect durable package authority."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]


class AuthorityHomeTests(unittest.TestCase):
    def test_private_fixture_home_preserves_account_storage_in_all_admitters(self) -> None:
        # A fresh interpreter exercises module constants as well as function calls.
        with tempfile.TemporaryDirectory() as directory:
            script = """
import importlib.util, json, sys
from pathlib import Path
repository = Path(sys.argv[1])
sys.path.insert(0, str(repository / 'Tools/parity'))
import run_lane
import qualify_finalized_package
import verify_local_qualification
values = {'runner': str(run_lane.FINALIZED_RETAINED),
          'controller': str(qualify_finalized_package.DEFAULT_RETAINED),
          'verifier': str(verify_local_qualification.QUALIFICATION_ROOT)}
for name in ('native_signing.py', 'prepare_finalized_package.py', 'consume-native-finalized-package.py'):
    spec = importlib.util.spec_from_file_location('probe_' + name.replace('-', '_'), repository / 'Tools/release' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values[name] = str(module.account_home())
    if name.startswith('consume'):
        values['consumer'] = str(module.SSD_IDENTITY)
print(json.dumps(values))
"""
            result = subprocess.run(
                [sys.executable, "-c", script, str(REPOSITORY)],
                env={**os.environ, "HOME": directory}, capture_output=True,
                text=True, check=True, timeout=15,
            )
            values = json.loads(result.stdout)
            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
            retained = home / "Library/Application Support/ContainerFamily/retained"
            self.assertEqual(values["runner"], str(retained / "devcontainer/finalized-admissions"))
            self.assertEqual(values["consumer"], str(retained / "workflow/ssd-volume.uuid"))
            self.assertEqual(values["controller"], str(retained / "devcontainer"))
            self.assertEqual(values["verifier"], str(retained / "devcontainer/qualifications"))
            for name in ("native_signing.py", "prepare_finalized_package.py", "consume-native-finalized-package.py"):
                self.assertEqual(values[name], str(home))
                self.assertNotEqual(values[name], directory)


if __name__ == "__main__":
    unittest.main()
