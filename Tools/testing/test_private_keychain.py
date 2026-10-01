"""No real keychains in unit tests: prove isolation, refusal and bounded cleanup."""

import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch

from private_keychain import KeychainAPI, operate, require_keychain_stopped, run_keychain
from case_evidence import canonical, digest


class PrivateKeychainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'owner.json').write_text(json.dumps({'root': str(self.root)}))
        environment = patch.dict(os.environ, HOME=str(self.root))
        environment.start()
        self.addCleanup(environment.stop)
        self.path = self.root / 'Library/Keychains/login.keychain-db'
        self.api = Mock()
        self.api.default.return_value = {'path': str(self.path), 'unlocked': True}

    def run_operation(self, action):
        return operate(self.root, action, api_factory=lambda: self.api)

    def test_create_selects_only_case_default_and_never_sets_global_preferences(self):
        self.assertEqual(self.run_operation('create'), {'status': 'ready', 'path': str(self.path), 'unlocked': True})
        self.api.create.assert_called_once_with(self.path.with_name('login.keychain'))
        self.assertEqual([call[0] for call in self.api.method_calls], ['create', 'default'])

    def test_absent_cleanup_never_loads_security_framework(self):
        self.assertEqual(self.run_operation('delete'), {'status': 'absent'})
        self.assertEqual(self.api.method_calls, [])

    def test_delete_is_limited_to_owned_regular_file_and_must_remove_it(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('fake keychain')
        self.api.delete.side_effect = lambda path: path.unlink()
        self.assertEqual(self.run_operation('delete'), {'status': 'deleted'})
        self.api.delete.assert_called_once_with(self.path)
        self.path.write_text('fake keychain')
        self.api.delete.side_effect = None
        with self.assertRaisesRegex(ValueError, 'survived'):
            self.run_operation('delete')

    def test_no_overwrite_wrong_default_or_locked_keychain(self):
        for observed in [{'path': '/operator/login.keychain-db', 'unlocked': True},
                         {'path': str(self.path), 'unlocked': False}]:
            self.api.default.return_value = observed
            with self.assertRaisesRegex(ValueError, 'unlocked case'):
                self.run_operation('create')
        self.path.write_text('existing')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_operation('create')

    def test_operator_home_wrong_owner_and_aliases_fail_before_api(self):
        with patch.dict(os.environ, HOME='/operator'):
            with self.assertRaisesRegex(ValueError, 'isolated'):
                self.run_operation('create')
        (self.root / 'owner.json').write_text('{"root":"/different"}')
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.run_operation('create')
        (self.root / 'owner.json').write_text(json.dumps({'root': str(self.root)}))
        (self.root / 'Library').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'aliased'):
            self.run_operation('create')
        self.assertEqual(self.api.method_calls, [])

    def test_hardlinked_or_non_file_cleanup_is_rejected(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('fixture')
        os.link(self.path, self.root / 'other')
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.run_operation('delete')
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.run_operation('delete')

    def helper_fixture(self):
        records = {}
        journal = Mock()
        journal.records.side_effect = lambda: records.copy()
        journal.put.side_effect = lambda name, payload: records.update({name: payload})
        child = Mock()
        child.process.pid = 123
        child.process.wait.return_value = 0
        child.start.side_effect = lambda _arguments, _root, log: log.write(b'{"status":"ready"}')
        return journal, records, child

    def test_child_uses_owned_process_and_journals_intent_before_launch(self):
        journal, records, child = self.helper_fixture()
        def start(arguments, root, log):
            self.assertIn(f'keychain-{child.start.call_count:04d}-intent.json', records)
            self.assertEqual(arguments[:2], ['/usr/bin/python3', '-I'])
            self.assertEqual(arguments[-2:], ['create' if child.start.call_count == 1 else 'delete', str(self.root)])
            self.assertEqual(root, self.root)
            log.write(b'{"status":"ready"}')
        child.start.side_effect = start
        with patch('host_runtime.OwnedProcess', return_value=child):
            self.assertEqual(run_keychain(self.root, 'create', journal), {'status': 'ready'})
            self.assertEqual(run_keychain(self.root, 'delete', journal), {'status': 'ready'})
        child.process.wait.assert_called_with(timeout=10)
        self.assertEqual(child.stop.call_count, 2)
        self.assertEqual(require_keychain_stopped(records), 2)
        self.assertIn('keychain-0002-result.json', records)

    def test_uncertain_spawn_or_stop_blocks_all_future_helpers(self):
        for phase in ('spawn', 'stop'):
            with self.subTest(phase=phase):
                journal, records, child = self.helper_fixture()
                if phase == 'spawn':
                    child.start.side_effect = RuntimeError('interrupted Popen')
                child.stop.side_effect = RuntimeError('ownership uncertain')
                with patch('host_runtime.OwnedProcess', return_value=child):
                    with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                        run_keychain(self.root, 'create', journal)
                    with self.assertRaisesRegex(ValueError, 'reconciliation'):
                        run_keychain(self.root, 'delete', journal)
                self.assertNotIn('keychain-0001-stopped.json', records)
                (self.root / 'keychain-0001.log').unlink()

    def test_failed_or_timed_out_child_must_stop_before_recovery(self):
        for failure in (1, subprocess.TimeoutExpired('private helper', 10)):
            journal, records, child = self.helper_fixture()
            if isinstance(failure, Exception):
                child.process.wait.side_effect = failure
            else:
                child.process.wait.return_value = failure
            with patch('host_runtime.OwnedProcess', return_value=child):
                with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                    run_keychain(self.root, 'create', journal)
            child.stop.assert_called_once()
            self.assertEqual(require_keychain_stopped(records), 1)
            self.assertNotIn('keychain-0001-result.json', records)
            self.assertEqual(records['keychain-0001.log'], b'{"status":"ready"}')
            (self.root / 'keychain-0001.log').unlink()

    def test_failed_create_then_successful_cleanup_keeps_private_diagnostics(self):
        journal, records, child = self.helper_fixture()
        child.start.side_effect = lambda _args, _root, log: log.write(b'OSStatus -25307 fixture')
        child.process.wait.return_value = 1
        with patch('host_runtime.OwnedProcess', return_value=child):
            with self.assertRaises(RuntimeError):
                run_keychain(self.root, 'create', journal)
            child.process.wait.return_value = 0
            child.start.side_effect = lambda _args, _root, log: log.write(b'{"status":"deleted"}')
            self.assertEqual(run_keychain(self.root, 'delete', journal), {'status': 'deleted'})
        self.assertEqual(records['keychain-0001.log'], b'OSStatus -25307 fixture')
        self.assertEqual(json.loads(records['keychain-0001-log.json'])['sha256'], digest(records['keychain-0001.log']))

    def test_partial_diagnostic_retention_resumes_without_relaunching_helper(self):
        journal, records, child = self.helper_fixture()
        def retain(name, payload):
            if name == 'keychain-0001-log.json':
                raise RuntimeError('retention interrupted')
            records[name] = payload
        journal.put.side_effect = retain
        with patch('host_runtime.OwnedProcess', return_value=child):
            with self.assertRaisesRegex(RuntimeError, 'retention'):
                run_keychain(self.root, 'create', journal)
            self.assertEqual(require_keychain_stopped(records), 1)
            journal.put.side_effect = lambda name, payload: records.update({name: payload})
            run_keychain(self.root, 'delete', journal)
        self.assertEqual(child.start.call_count, 2)
        self.assertIn('keychain-0001-log.json', records)

    def test_missing_mismatched_or_false_stop_receipt_is_not_completion(self):
        intent = canonical({'arguments': ['fixture']})
        for stopped in (None, {}, {'verifiedStopped': False},
                        {'verifiedStopped': True, 'intentSHA256': 'wrong'}):
            records = {'keychain-0001-intent.json': intent,
                       'keychain-0001-stopped.json': canonical(stopped)}
            with self.assertRaisesRegex(ValueError, 'reconciliation'):
                require_keychain_stopped(records)
        records['keychain-0001-stopped.json'] = canonical({'verifiedStopped': True, 'intentSHA256': digest(intent)})
        self.assertEqual(require_keychain_stopped(records), 1)
        with self.assertRaisesRegex(ValueError, 'durable'):
            run_keychain(self.root, 'create', None)

    def test_legacy_keychain_is_not_treated_as_absent(self):
        self.path.parent.mkdir(parents=True)
        self.path.with_name('login.keychain').write_text('legacy fixture')
        with self.assertRaisesRegex(ValueError, 'legacy'):
            self.run_operation('delete')

    def test_status_failure_and_unknown_action_are_not_hidden(self):
        KeychainAPI.check(0)
        with self.assertRaisesRegex(RuntimeError, 'OSStatus -25307'):
            KeychainAPI.check(-25307)
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            self.run_operation('reset')

    def test_security_calls_release_handles_and_never_allow_authentication_ui(self):
        security, core = Mock(), Mock()
        security.SecKeychainSetUserInteractionAllowed.return_value = 0
        def reference(*args):
            args[-1]._obj.value = 42
            return 0
        security.SecKeychainCreate.side_effect = reference
        security.SecKeychainCopyDefault.side_effect = reference
        security.SecKeychainOpen.side_effect = reference
        security.SecKeychainDelete.return_value = 0
        def path(_reference, _size, buffer):
            buffer.value = os.fsencode(self.path)
            return 0
        security.SecKeychainGetPath.side_effect = path
        def status(_reference, flags):
            flags._obj.value = 1
            return 0
        security.SecKeychainGetStatus.side_effect = status
        with patch('private_keychain.ctypes.CDLL', side_effect=[security, core]):
            api = KeychainAPI()
        api.create(self.path)
        self.assertEqual(api.default(), {'path': str(self.path), 'unlocked': True})
        api.delete(self.path)
        security.SecKeychainSetUserInteractionAllowed.assert_called_once_with(False)
        self.assertFalse(security.SecKeychainCreate.call_args.args[3])
        self.assertEqual(core.CFRelease.call_count, 3)
        security.SecKeychainGetPath.side_effect = None
        security.SecKeychainGetPath.return_value = -1
        with self.assertRaises(RuntimeError):
            api.default()
        self.assertEqual(core.CFRelease.call_count, 4)


if __name__ == '__main__':
    unittest.main()
