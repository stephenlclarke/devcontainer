"""Regressions for exact source legal-file admission."""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('legal', Path(__file__).with_name('prepare-native-legal.py'))
legal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(legal)


class LegalTests(unittest.TestCase):
    def test_api_uses_bounded_noninteractive_transport(self):
        with patch.object(legal.subprocess, 'run', return_value=SimpleNamespace(stdout=b'{"sha":"object"}')) as run:
            self.assertEqual(legal.api('repos/example/dependency/git/commits/object'), {'sha': 'object'})
        run.assert_called_once_with(['gh', 'api', 'repos/example/dependency/git/commits/object'],
            stdin=legal.subprocess.DEVNULL, capture_output=True, check=True, timeout=30)

    def test_api_rejects_oversized_nonobject_and_invalid_json_responses(self):
        for response in [b'123456789', b'[]', b'not json']:
            with self.subTest(response=response), patch.object(legal, 'MAX_LEGAL_BYTES', 1), \
                    patch.object(legal.subprocess, 'run', return_value=SimpleNamespace(stdout=response)), \
                    self.assertRaises(ValueError):
                legal.api('repos/example/dependency/git/commits/object')

    def fixture(self):
        data = b'Apache License Version 2.0\n'
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        dependency = SimpleNamespace(identity='dependency', location='https://github.com/example/dependency.git',
                                     revision='a' * 40, version='1.0.0', license='Apache-2.0')
        endpoint = 'repos/example/dependency/git/'
        responses = {
            endpoint + 'commits/' + 'a' * 40: {'sha': 'a' * 40, 'tree': {'sha': 'b' * 40}},
            endpoint + 'trees/' + 'b' * 40: {'sha': 'b' * 40, 'truncated': False,
                                          'tree': [{'path': 'LICENSE', 'sha': blob, 'type': 'blob', 'mode': '100644'}]},
            endpoint + 'blobs/' + blob: {'sha': blob, 'encoding': 'base64', 'size': len(data),
                                       'content': base64.b64encode(data).decode()}}
        return dependency, responses, blob

    def test_collect_and_render_exact_provenance_and_legal_text(self):
        dependency, responses, blob = self.fixture()
        observed = []
        rows = legal.collect([dependency], lambda dep, text: observed.append((dep, text)), responses.__getitem__)
        self.assertEqual(rows[0]['files'][0]['gitBlob'], blob)
        self.assertEqual(observed[0][0], dependency)
        text = legal.render(rows)
        self.assertIn('Dependency: dependency\nVersion: 1.0.0\nRevision: ' + 'a' * 40, text)
        self.assertIn('Apache License Version 2.0', text)

    def test_foreign_or_credentialed_repository_is_rejected(self):
        for location in ['http://github.com/a/b', 'https://other.example/a/b',
                         'https://user:password@github.com/a/b', 'https://github.com/a/b?ref=main',
                         'https://github.com/a/b/tree/main', 'https://github.com/a/.',
                         'https://github.com//a/b', 'https://github.com/a/b/']:
            with self.subTest(location=location), self.assertRaises(ValueError):
                legal.github_repository(location)

    def test_changed_commit_or_root_tree_is_rejected(self):
        for category in ['commits', 'trees']:
            dependency, responses, _ = self.fixture()
            target = next(key for key in responses if '/' + category + '/' in key)
            responses[target]['sha'] = 'c' * 40
            with self.assertRaises(ValueError):
                legal.collect([dependency], lambda *_: None, responses.__getitem__)

    def test_blob_bytes_cannot_change_with_unchanged_git_identity(self):
        dependency, responses, blob = self.fixture()
        response = responses['repos/example/dependency/git/blobs/' + blob]
        response['content'] = base64.b64encode(b'changed').decode()
        response['size'] = 7
        with self.assertRaisesRegex(ValueError, 'selected Git object'):
            legal.collect([dependency], lambda *_: None, responses.__getitem__)

    def test_incomplete_duplicate_or_missing_legal_tree_is_rejected(self):
        for tree in [{'truncated': True, 'tree': []}, {'truncated': False, 'tree': []},
                     {'truncated': False, 'tree': [{'path': 'LICENSE'}, {'path': 'LICENSE'}]}]:
            with self.subTest(tree=tree), self.assertRaises(ValueError):
                legal.legal_names(tree)

    def test_symlink_license_is_not_read(self):
        with self.assertRaises(ValueError):
            legal.legal_names({'truncated': False, 'tree': [
                {'path': 'LICENSE', 'type': 'blob', 'mode': '120000', 'sha': 'c' * 40}]})

    def test_copying_fallback_and_all_notices_are_preserved(self):
        rows = legal.legal_names({'truncated': False, 'tree': [
            {'path': name, 'type': 'blob', 'mode': '100644'} for name in ['NOTICE', 'COPYING']]})
        self.assertEqual([row['path'] for row in rows], ['COPYING', 'NOTICE'])

    def test_declared_license_mismatch_is_not_accepted(self):
        dependency, responses, _ = self.fixture()
        def reject(*_):
            raise ValueError('License mismatch')
        with self.assertRaisesRegex(ValueError, 'License mismatch'):
            legal.collect([dependency], reject, responses.__getitem__)

    def test_size_and_encoding_fail_closed(self):
        for update in [{'size': 0}, {'encoding': 'raw'}, {'sha': 'd' * 40}]:
            dependency, responses, blob = self.fixture()
            responses['repos/example/dependency/git/blobs/' + blob].update(update)
            with self.assertRaises(ValueError):
                legal.collect([dependency], lambda *_: None, responses.__getitem__)

    def test_legal_filename_cannot_inject_dependency_provenance(self):
        with self.assertRaisesRegex(ValueError, 'unsafe provenance'):
            legal.read_blob('example/dependency', {'path': 'LICENSE\nDependency: injected', 'sha': 'a' * 40})

    def test_main_selects_profile_ledger_and_seals_exact_notices(self):
        dependency, responses, _ = self.fixture()
        real_collect = legal.collect
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            resolved = root / 'Package.resolved'
            ledger = root / 'licenses.json'
            resolved.write_text(json.dumps({'pins': [{'identity': dependency.identity,
                'location': dependency.location, 'state': {'revision': dependency.revision,
                                                          'version': dependency.version}}]}))
            ledger.write_text(json.dumps({'schemaVersion': 1, 'licenses': {
                'dependency': 'Apache-2.0', 'unused-enhanced-only': 'MIT'}}))
            output = root / 'sealed'
            arguments = ['prepare-native-legal', '--resolved', str(resolved),
                '--license-manifest', str(ledger), '--release-tools', str(Path(__file__).parent),
                '--output', str(output)]
            with patch('sys.argv', arguments), patch.object(legal, 'collect', side_effect=
                    lambda deps, checker: real_collect(deps, checker, responses.__getitem__)):
                legal.main()
            result = json.loads((output / 'legal.json').read_text())
            selected = json.loads((output / 'dependency-licenses.json').read_text())
            self.assertEqual(selected['licenses'], {'dependency': 'Apache-2.0'})
            self.assertEqual(result['resolvedSHA256'], legal.digest(resolved.read_bytes()))
            self.assertEqual(result['noticesSHA256'], legal.digest((output / 'THIRD-PARTY-NOTICES.txt').read_bytes()))
            self.assertEqual(len(result['dependencies']), 1)
            with patch('sys.argv', arguments), self.assertRaisesRegex(ValueError, 'fresh physical'):
                legal.main()

    def test_source_or_policy_drift_cannot_seal_a_legal_bundle(self):
        dependency, responses, _ = self.fixture()
        real_collect = legal.collect
        for changed in ['Package.resolved', 'licenses.json', 'dependency_metadata.py',
                        'write-third-party-notices.py']:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                tools = root / 'release-tools'
                tools.mkdir()
                for name in ['dependency_metadata.py', 'write-third-party-notices.py']:
                    shutil.copyfile(Path(__file__).parent / name, tools / name)
                resolved = root / 'Package.resolved'
                ledger = root / 'licenses.json'
                resolved.write_text(json.dumps({'pins': [{'identity': dependency.identity,
                    'location': dependency.location, 'state': {'revision': dependency.revision,
                                                              'version': dependency.version}}]}))
                ledger.write_text(json.dumps({'schemaVersion': 1, 'licenses': {'dependency': 'Apache-2.0'}}))
                output = root / 'unsealed'
                arguments = ['prepare-native-legal', '--resolved', str(resolved),
                    '--license-manifest', str(ledger), '--release-tools', str(tools), '--output', str(output)]

                def collect_then_change(dependencies, checker):
                    rows = real_collect(dependencies, checker, responses.__getitem__)
                    path = tools / changed if changed.endswith('.py') else root / changed
                    path.write_bytes(path.read_bytes() + b'\n')
                    return rows

                with patch('sys.argv', arguments), patch.object(legal, 'collect',
                        side_effect=collect_then_change), self.assertRaisesRegex(ValueError, 'changed during'):
                    legal.main()
                self.assertTrue(output.is_dir())
                self.assertFalse((output / 'legal.json').exists())
                self.assertFalse((output / 'THIRD-PARTY-NOTICES.txt').exists())


if __name__ == '__main__':
    unittest.main()
