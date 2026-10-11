#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prove ordinary caller umask cannot alter retained package directory inventories."""
from __future__ import annotations
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import types
import unittest

REPO = Path(__file__).resolve().parents[2]
QUALIFY_SOURCE = REPO / 'Tools/parity/qualify_finalized_package.py'

sys.path.insert(0, str(REPO/'Tools/bazel'))
import prepare_releases


def load_qualifier(path: Path):
    spec = importlib.util.spec_from_file_location('qualifier_umask_test', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load qualification controller')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AdmissionReached(Exception):
    pass


class QualificationUmaskReceiptStabilityTest(unittest.TestCase):
    def test_main_sets_private_umask_before_real_tar_admission(self):
        self.assertTrue(QUALIFY_SOURCE.is_file(), QUALIFY_SOURCE)
        with tempfile.TemporaryDirectory(prefix='qualify-umask-regression-') as temporary:
            root = Path(temporary).resolve(strict=True)
            source = root/'container-homebrew-arm64.tar.gz'
            with tarfile.open(source, 'w:gz') as archive:
                for dirname in ('bin', 'share', 'share/config'):
                    info = tarfile.TarInfo(dirname+'/')
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    info.mtime = 1
                    archive.addfile(info)
                for name, body in (('bin/container', b'container'),
                                   ('bin/container-engine', b'engine'),
                                   ('bin/container-apiserver', b'apiserver'),
                                   ('share/config/runtime.json', b'{"mode":"fixture"}\n')):
                    info = tarfile.TarInfo(name)
                    info.size = len(body)
                    info.mode = 0o755 if name.startswith('bin/') else 0o644
                    info.mtime = 1
                    archive.addfile(info, io.BytesIO(body))
            asset = {'repository':'stephenlclarke/container','tag':'1.1.0',
                     'name':'container-homebrew-arm64.tar.gz','size':source.stat().st_size,
                     'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
            receipts=root/'retained-receipts'; receipts.mkdir(mode=0o700)
            baseline_root=root/'baseline-ssd'; baseline_root.mkdir(mode=0o700)
            caller_umask=os.umask(0)
            try:
                os.umask(0o077)
                baseline=prepare_releases.prepare(asset,source,baseline_root,receipts)
                baseline_receipt=receipts/(baseline['preparationSHA256']+'.json')
                baseline_bytes=baseline_receipt.read_bytes()
                baseline_inventory=json.loads(baseline_bytes)['inventory']
                baseline_directories={name:row['mode'] for name,row in baseline_inventory.items()
                                      if row['kind']=='directory'}
                self.assertTrue(baseline_directories)
                self.assertEqual(set(baseline_directories.values()),{0o700})

                fresh_ssd=root/'fresh-ssd'; fresh_ssd.mkdir(mode=0o700)
                repository=root/'repository'; repository.mkdir(mode=0o700)
                qualification_ssd=root/'qualification-ssd'; qualification_ssd.mkdir(mode=0o700)
                retained=root/'qualification-retained'; retained.mkdir(mode=0o700)
                evidence=qualification_ssd/'evidence'
                args=types.SimpleNamespace(repository=repository,ssd_root=qualification_ssd,
                    retained_root=retained,qualification_directory=retained/'qualifications',
                    evidence=evidence,execute=True,component_fixture='E13-compose-signals',
                    finalized_directory=retained/'finalized-package',
                    accepted_state=retained/'accepted-state',
                    source_commit='0'*40,provenance_sha256='1'*64,state_sha256='2'*64)
                qualifier=load_qualifier(QUALIFY_SOURCE)
                qualifier.parse_args=lambda:args
                def validate_inputs(selected, *, candidate_diagnostic=False):
                    self.assertFalse(candidate_diagnostic)
                    self.assertIsNotNone(selected.finalized_directory)
                    self.assertIsNotNone(selected.accepted_state)
                    selected._manifest={}
                    return {}
                qualifier.validate_inputs=validate_inputs
                qualifier.require_native_provider_socket_layouts=lambda _root:None
                def admit_before_runtime(_args):
                    result=prepare_releases.prepare(asset,source,fresh_ssd,receipts)
                    inventory=json.loads(baseline_bytes)['inventory']
                    fresh_inventory=prepare_releases.inventory(Path(result['root']))
                    fresh_dirs={name:row['mode'] for name,row in fresh_inventory.items()
                                if row['kind']=='directory'}
                    self.assertTrue(fresh_dirs)
                    self.assertEqual(set(fresh_dirs.values()),{0o700})
                    self.assertEqual(fresh_inventory,inventory)
                    self.assertEqual(baseline_receipt.read_bytes(),baseline_bytes)
                    raise AdmissionReached()
                qualifier.admit_package_before_runtime=admit_before_runtime
                # Model a shell with its ordinary 022 umask. main() must change it
                # before admission performs the actual archive extraction.
                os.umask(0o022)
                with self.assertRaises(AdmissionReached):
                    qualifier.main()
            finally:
                os.umask(caller_umask)


if __name__=='__main__':
    unittest.main()
