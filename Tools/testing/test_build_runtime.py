"""Released builder admission/ownership tests without touching host services."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from build_runtime import ReleasedBuilder, admit_builder, host_build_dns, native_blob, require_native_image
from case_evidence import canonical, digest
from guest_runtime import require_guest_cleanup
from service_journal import ServiceJournal


class BuildRuntimeTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        (self.root / 'container').mkdir()
        self.lock = json.loads((Path(__file__).parents[1] / 'bazel/builder-images.lock.json').read_text())
        image = dict(self.lock['images'][0])
        config = self.blob({'os': 'linux', 'architecture': 'arm64'})
        manifest = self.blob({'schemaVersion': 2, 'config': config, 'layers': []})
        image.update(manifest=manifest['digest'], config=config['digest'])
        self.native_descriptor = self.blob({'schemaVersion': 2, 'manifests': [manifest],
                                           'annotations': {'com.apple.containerization.index.indirect': 'true'}})
        self.native_descriptor['mediaType'] = 'application/vnd.oci.image.index.v1+json'
        self.inputs = {'image': image, 'path': '/retained/owned-builder.tar', 'dnsArguments': []}
        dns = patch('build_runtime.host_build_dns', return_value=[])
        self.dns = dns.start()
        self.addCleanup(dns.stop)
        self.journal = ServiceJournal(self.root / 'journal.sqlite', {'case': 'build'}, create=True)
        self.inventory, self.calls = [], []
        self.fail_start, self.ignore_delete = False, False
        self.builder = ReleasedBuilder(self.inputs, self.root, self.journal, self.command)

    def blob(self, document):
        payload = canonical(document)
        identifier = digest(payload)
        path = self.root / 'container/content/blobs/sha256' / identifier
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return {'digest': 'sha256:' + identifier, 'size': len(payload)}

    def record(self):
        return {'configuration': {'id': 'buildkit',
                'image': {'reference': self.builder.reference, 'descriptor': dict(self.native_descriptor)},
                'labels': {'com.apple.container.plugin': 'builder', 'com.apple.container.resource.role': 'builder'},
                'mounts': [{'source': str(self.root / 'container/builder'),
                            'destination': '/var/lib/container-builder-shim/exports'}]}, 'status': {'state': 'running'}}

    def command(self, name, arguments, *, timeout=60):
        self.calls.append(arguments)
        self.journal.put(name + '-intent.json', canonical({'arguments': arguments}))
        payload = b''
        if arguments[0] == 'list':
            payload = canonical(self.inventory)
        elif arguments[:2] == ['builder', 'start']:
            self.inventory = [self.record()]
            self.inventory[0]['configuration']['dns'] = {'nameservers': arguments[3::2]}
            if self.fail_start:
                raise TimeoutError('uncertain start')
        elif arguments == ['builder', 'stop']:
            self.inventory[0]['status']['state'] = 'stopped'
        elif arguments == ['builder', 'delete'] and not self.ignore_delete:
            self.inventory = []
        self.journal.put(name + '-stopped.json', canonical({'verifiedStopped': True}))
        self.journal.put(name + '.log', payload)
        self.journal.put(name + '-log.json', canonical({'bytes': len(payload), 'sha256': digest(payload), 'truncated': False}))
        return payload

    def test_both_released_builder_admissions_are_digest_preserving(self):
        with patch('build_runtime.require_image', side_effect=lambda image, root: {'image': image}) as require:
            for lane, image in zip(('apple-stock', 'container-compose'), self.lock['images']):
                self.assertEqual(admit_builder(self.lock, lane, self.root), {'image': image, 'dnsArguments': []})
            self.assertEqual(require.call_count, 2)

    def test_builder_uses_exact_loaded_local_reference_without_digest_alias_assumption(self):
        # Stock ClientImage.get matches stored reference/name, not the digest
        # descriptor. A tagged archive is not a second digest-named record.
        for image in self.lock['images']:
            builder = ReleasedBuilder({'image': image, 'path': '/retained/builder.tar', 'dnsArguments': []},
                                       self.root, self.journal, self.command)
            loaded_references = {image['reference']}
            self.assertIn(builder.reference, loaded_references)
            self.assertIn(image['manifest'], canonical(builder.intent).decode())

    def test_missing_duplicate_wrong_release_and_malformed_builders_fail_before_loading(self):
        for lock in ({}, dict(self.lock, images=[]), dict(self.lock, images=self.lock['images'] * 2)):
            with patch('build_runtime.require_image') as require, self.assertRaises(ValueError):
                admit_builder(lock, 'apple-stock', self.root)
            require.assert_not_called()
        wrong = copy.deepcopy(self.lock)
        wrong['images'][0]['reference'] = wrong['images'][0]['repository'] + ':latest'
        with self.assertRaises(ValueError):
            admit_builder(wrong, 'apple-stock', self.root)
        with self.assertRaises(ValueError):
            admit_builder(self.lock, 'other', self.root)

    def test_private_leaf_selection_owned_shutdown_and_recovery_receipt(self):
        self.builder.provision()
        self.assertIn(self.inputs['image']['reference'], self.builder.config.read_text())
        self.assertIn(['image', 'load', '--input', self.inputs['path']], self.calls)
        self.builder.cleanup()
        self.builder.cleanup()
        self.assertEqual(self.calls.count(['builder', 'delete']), 1)
        require_guest_cleanup(self.journal.records())
        self.assertFalse(self.inventory)

    def test_preexisting_containers_or_config_are_not_adopted(self):
        self.inventory = [self.record()]
        with self.assertRaisesRegex(ValueError, 'refusing builder adoption'):
            self.builder.provision()
        self.assertNotIn('e04-builder-intent.json', self.journal.records())
        self.inventory = []
        self.builder.config.parent.mkdir()
        self.builder.config.write_bytes(b'foreign')
        with self.assertRaises(FileExistsError):
            self.builder.provision()
        self.assertEqual(self.builder.config.read_bytes(), b'foreign')

    def test_uncertain_start_never_deletes_or_claims_recovery(self):
        self.fail_start = True
        with self.assertRaises(TimeoutError):
            self.builder.provision()
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            self.builder.cleanup()
        self.assertNotIn(['builder', 'delete'], self.calls)
        with self.assertRaisesRegex(ValueError, 'explicit reconciliation'):
            require_guest_cleanup(self.journal.records())

    def test_identity_change_and_wrong_export_mount_block_cleanup(self):
        self.builder.provision()
        original = copy.deepcopy(self.inventory)
        for field, value in (('id', 'foreign'), ('labels', {}), ('mounts', []), ('image', {})):
            self.inventory = copy.deepcopy(original)
            self.inventory[0]['configuration'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity'):
                self.builder.cleanup()
        self.inventory = copy.deepcopy(original)
        self.inventory[0]['configuration']['extra'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'configuration changed'):
            self.builder.cleanup()
        self.assertNotIn(['builder', 'stop'], self.calls)

    def test_config_tamper_and_extra_container_fail_closed(self):
        self.builder.provision()
        self.builder.config.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'configuration changed'):
            self.builder.cleanup()
        self.builder.config.write_bytes(self.builder.payload)
        self.inventory *= 2
        with self.assertRaisesRegex(ValueError, 'exactly'):
            self.builder.cleanup()

    def test_pending_stop_delete_residue_and_reappeared_worker_preserve_runtime(self):
        self.builder.provision()
        self.inventory[0]['status']['state'] = 'stopping'
        with self.assertRaisesRegex(ValueError, 'not stopped'):
            self.builder.cleanup()
        self.inventory[0]['status']['state'] = 'stopped'
        self.ignore_delete = True
        with self.assertRaisesRegex(ValueError, 'residue'):
            self.builder.cleanup()

    def test_successful_cleanup_rejects_reappeared_container(self):
        self.builder.provision()
        self.builder.cleanup()
        self.inventory = [self.record()]
        with self.assertRaisesRegex(ValueError, 'reappeared'):
            self.builder.cleanup()

    def test_unstarted_worker_cleanup_does_not_start_it(self):
        self.builder.cleanup()
        self.journal.put('e04-builder-intent.json', canonical(self.builder.intent))
        self.builder.cleanup()
        self.assertNotIn(['builder', 'start'], self.calls)

    def test_lost_delete_response_reconciles_absence_without_repeating_mutation(self):
        self.builder.provision()
        def delete_then_interrupt(name, arguments, **kwargs):
            result = self.command(name, arguments, **kwargs)
            if arguments == ['builder', 'delete']:
                raise ConnectionError('lost response')
            return result
        self.builder.command = delete_then_interrupt
        with self.assertRaises(ConnectionError):
            self.builder.cleanup()
        self.builder.command = self.command
        self.builder.cleanup()
        self.assertEqual(self.calls.count(['builder', 'delete']), 1)

    def test_repeated_provision_and_wrong_journal_cannot_mutate(self):
        self.builder.provision()
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.builder.provision()
        self.builder.intent = dict(self.builder.intent, root='/foreign')
        with self.assertRaisesRegex(ValueError, 'another transaction'):
            self.builder.cleanup()

    def test_absent_or_malformed_inventory_and_unstarted_appearance_fail_closed(self):
        with patch.object(self.builder, 'command', return_value=b'{}'), self.assertRaisesRegex(ValueError, 'malformed'):
            self.builder.inventory()
        self.journal.put('e04-builder-intent.json', canonical(self.builder.intent))
        self.inventory = [self.record()]
        with self.assertRaisesRegex(ValueError, 'Unstarted builder'):
            self.builder.cleanup()

    def test_dns_matches_real_build_and_changes_do_not_adopt_a_recreated_worker(self):
        self.dns.return_value = ['--dns', '1.1.1.1', '--dns', 'fe80::1%en0']
        self.inputs['dnsArguments'] = list(self.dns.return_value)
        self.builder = ReleasedBuilder(self.inputs, self.root, self.journal, self.command)
        self.builder.provision()
        config = self.inventory[0]['configuration']
        # Model BuilderStart's nonempty requested-DNS comparison: mismatched
        # DNS would trigger stop/delete/create rather than reuse this identity.
        self.assertEqual(config['dns']['nameservers'], self.dns.return_value[1::2])
        self.builder.verify_for_build()
        self.dns.return_value = ['--dns', '9.9.9.9']
        with self.assertRaisesRegex(ValueError, 'DNS changed'):
            self.builder.verify_for_build()
        # A changed host resolver does not prevent deleting the still-exact
        # owned worker. Only build admission needs current host DNS equality.
        self.builder.cleanup()

    def test_dns_parser_matches_pinned_product_order_ipv4_ipv6_and_invalid_entries(self):
        resolver = self.root / 'resolv.conf'
        resolver.write_text('search example.com\nnameserver 1.1.1.1\nnameserver invalid\n'
                            'nameserver 1.1.1.1\nnameserver fe80::1%en0\nnameserver 999.1.2.3\n')
        self.assertEqual(host_build_dns(resolver), ['--dns', '1.1.1.1', '--dns', 'fe80::1%en0'])
        resolver.write_bytes(b'\xff')
        self.assertEqual(host_build_dns(resolver), [])
        self.assertEqual(host_build_dns(self.root / 'missing'), [])

    def test_native_index_leaf_and_config_are_distinct_and_authenticated(self):
        self.assertNotEqual(self.native_descriptor['digest'], self.inputs['image']['manifest'])
        require_native_image(self.root, self.native_descriptor, self.inputs['image'])
        for field in ('manifest', 'config'):
            wrong = dict(self.inputs['image'], **{field: 'sha256:' + 'f' * 64})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'admitted'):
                require_native_image(self.root, self.native_descriptor, wrong)
        path = self.root / 'container/content/blobs/sha256' / self.native_descriptor['digest'].split(':')[1]
        payload = path.read_bytes()
        path.write_bytes(b'x' * len(payload))
        with self.assertRaisesRegex(ValueError, 'differs'):
            require_native_image(self.root, self.native_descriptor, self.inputs['image'])
        path.unlink()
        path.symlink_to(self.root / 'foreign')
        with self.assertRaisesRegex(ValueError, 'canonical'):
            require_native_image(self.root, self.native_descriptor, self.inputs['image'])

    def test_native_metadata_bounds_shapes_and_platform_fail_closed(self):
        for descriptor in ({}, {'digest': '../escape', 'size': 1},
                            dict(self.native_descriptor, size=True), dict(self.native_descriptor, size=1024**2 + 1)):
            with self.subTest(descriptor=descriptor), self.assertRaisesRegex(ValueError, 'descriptor'):
                native_blob(self.root, descriptor)
        for document in ([], {'schemaVersion': 2, 'manifests': []}, {'schemaVersion': 2, 'manifests': [{}, {}]}):
            descriptor = dict(self.blob(document), mediaType='application/vnd.oci.image.index.v1+json')
            with self.subTest(document=document), self.assertRaises(ValueError):
                require_native_image(self.root, descriptor, self.inputs['image'])
        config = self.blob({'os': 'linux', 'architecture': 'amd64'})
        manifest = self.blob({'config': config})
        index = dict(self.blob({'schemaVersion': 2, 'manifests': [manifest]}),
                     mediaType='application/vnd.oci.image.index.v1+json')
        image = dict(self.inputs['image'], manifest=manifest['digest'], config=config['digest'])
        with self.assertRaisesRegex(ValueError, 'not linux-arm64'):
            require_native_image(self.root, index, image)
        wrong_size = dict(index, size=index['size'] + 1)
        with self.assertRaisesRegex(ValueError, 'bounded owned file'):
            native_blob(self.root, wrong_size)

    def test_dns_change_before_start_and_unresolved_delete_are_not_retried(self):
        self.dns.return_value = ['--dns', '9.9.9.9']
        with self.assertRaisesRegex(ValueError, 'DNS changed before'):
            self.builder.provision()
        self.assertNotIn(['builder', 'start'], self.calls)
        self.builder.cleanup()

    def test_worker_must_be_running_and_failed_delete_keeps_quarantine(self):
        def stopped_start(name, arguments, **kwargs):
            payload = self.command(name, arguments, **kwargs)
            if arguments[:2] == ['builder', 'start']:
                self.inventory[0]['status']['state'] = 'stopped'
            return payload
        self.builder.command = stopped_start
        with self.assertRaisesRegex(ValueError, 'running state'):
            self.builder.provision()
        self.ignore_delete = True
        with self.assertRaisesRegex(ValueError, 'residue'):
            self.builder.cleanup()
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.builder.cleanup()
        self.assertEqual(self.calls.count(['builder', 'delete']), 1)


if __name__ == '__main__':
    unittest.main()
