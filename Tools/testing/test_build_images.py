"""E04 image cleanup/recovery through a real disposable Unix HTTP endpoint."""

import copy
import json
import os
from pathlib import Path
from socketserver import UnixStreamServer
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from build_images import BuildImages
from build_probe import tag_for
from guest_fixture import GuestFixture, OWNER_LABEL
from service_journal import ServiceJournal
import test_guest_fixture as helpers


OWNER = "a" * 64
BASE = "docker.io/library/alpine@sha256:" + "b" * 64
SUCCESS = b'{"stream":"Step 4/4 done"}\n'
FAILURE = b'{"stream":"RUN false"}\n{"error":"exit 1"}\n'


class Handler(helpers.Handler):
    def dispatch(self):
        server = self.server
        url = urlsplit(self.path)
        path = url.path.removeprefix("/v1.53")
        server.routes.append((self.command, self.path))
        if path == "/images/json":
            filters = json.loads(parse_qs(url.query)["filters"][0])
            label = filters["label"][0].split("=", 1)
            return 200, [image for image in server.images.values()
                         if image.get("Config", {}).get("Labels", {}).get(label[0]) == label[1]]
        reference = unquote(path.removeprefix("/images/").removesuffix("/json"))
        image = server.images.get(reference)
        if image is None:
            image = next((item for item in server.images.values() if item["Id"] == reference), None)
        if self.command == "GET":
            return (200, image) if image is not None else (404, {"message": "missing"})
        if self.command == "DELETE":
            server.delete_records.append(server.journal.records())
            if not server.ignore_delete:
                server.images = {key: value for key, value in server.images.items() if value["Id"] != reference}
            return 200, [{"Deleted": reference}]
        return 400, {"message": "unexpected request"}


class BuildImagesTests(unittest.TestCase):
    stop = helpers.GuestFixtureTests.stop

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.socket = self.root / "s"
        self.server = UnixStreamServer(str(self.socket), Handler)
        self.server.routes, self.server.delete_records = [], []
        self.server.ignore_delete = False
        self.image = "sha256:" + "c" * 64
        self.server.images = {BASE: {"Id": self.image, "RepoDigests": [BASE]}}
        self.journal = ServiceJournal(self.root / "build.sqlite", {"case": OWNER}, create=True)
        self.server.journal = self.journal
        self.client = GuestFixture(self.socket, OWNER, self.image, "1.53", self.journal)
        self.fixture = self.reopen()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.stop)

    def reopen(self):
        journal = ServiceJournal(self.journal.path, {"case": OWNER})
        return BuildImages(self.client, journal, OWNER, BASE)

    def output(self, *, failing=False):
        tag = tag_for(OWNER, failing=failing)
        self.server.images[tag] = {"Id": "sha256:" + ("e" if failing else "d") * 64,
                                   "RepoTags": [tag], "RepoDigests": [],
                                   "Config": {"Labels": {OWNER_LABEL: OWNER, "devcontainer.parity": "true"}}}
        return self.server.images[tag]

    def submitted(self):
        self.fixture.prepare()
        self.fixture.start()
        self.output()
        self.fixture.response(200, SUCCESS)

    def assert_no_delete(self):
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_docker_hub_official_digest_display_is_the_same_admitted_reference(self):
        self.server.images[BASE]['RepoDigests'] = [BASE.removeprefix('docker.io/library/')]
        self.fixture.prepare()
        self.assertIn('e04-images-intent.json', self.journal.records())

    def test_foreign_registry_or_namespace_cannot_satisfy_base_admission(self):
        for reference in ('other.example/library/alpine@sha256:' + 'b' * 64,
                          'example/alpine@sha256:' + 'b' * 64, 'alpine@sha256:' + 'c' * 64):
            self.server.images[BASE]['RepoDigests'] = [reference]
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, 'not prepared'):
                self.fixture.prepare()
        self.assertNotIn('e04-images-intent.json', self.journal.records())

    def test_containerd_local_output_digest_is_bound_to_exact_owned_repository_and_id(self):
        self.fixture.prepare()
        self.fixture.start()
        image = self.output()
        image['RepoDigests'] = [tag_for(OWNER).split(':')[0] + '@' + image['Id']]
        image['Descriptor'] = {'digest': image['Id'], 'mediaType': 'application/vnd.oci.image.manifest.v1+json'}
        self.fixture.response(200, SUCCESS)
        self.reopen().cleanup()
        self.assertEqual(list(self.server.images), [BASE])

    def test_owned_repository_digest_needs_matching_content_descriptor(self):
        image = self.output()
        image['RepoDigests'] = [tag_for(OWNER).split(':')[0] + '@' + image['Id']]
        for descriptor in (None, {}, {'digest': 'sha256:' + 'f' * 64}):
            image['Descriptor'] = descriptor
            with self.subTest(descriptor=descriptor), self.assertRaisesRegex(ValueError, 'repository digest'):
                self.fixture.identity(image, False)

    def test_both_intents_precede_build_and_cleanup_is_exact_and_repeatable(self):
        self.submitted()
        self.fixture.start(failing=True)
        self.assertTrue(self.fixture.response(200, FAILURE, failing=True).failed)
        self.reopen().cleanup()
        self.reopen().cleanup()
        records = self.journal.records()
        self.assertEqual(set(json.loads(records["e04-images-intent.json"])["contexts"]), {"built", "failed"})
        self.assertEqual(records["e04-built-response.jsonl"], SUCCESS)
        self.assertEqual(records["e04-failed-response.jsonl"], FAILURE)
        self.assertIn("e04-images-removed.json", records)
        deletes = [route for method, route in self.server.routes if method == "DELETE"]
        self.assertEqual(deletes, ["/v1.53/images/sha256%3A" + "d" * 64 + "?force=false&noprune=true"])
        self.assertIn("e04-built-delete.json", self.server.delete_records[0])
        self.assertEqual(list(self.server.images), [BASE])

    def test_preexisting_output_and_missing_or_wrong_base_fail_before_intent(self):
        initial = copy.deepcopy(self.server.images)
        for mutation in ("missing", "wrong-id", "wrong-digest", "invalid-digests", "existing-output"):
            self.server.images = copy.deepcopy(initial)
            if mutation == "missing":
                self.server.images.clear()
            elif mutation == "wrong-id":
                self.server.images[BASE]["Id"] = "sha256:" + "f" * 64
            elif mutation == "wrong-digest":
                self.server.images[BASE]["RepoDigests"] = []
            elif mutation == "invalid-digests":
                self.server.images[BASE]["RepoDigests"] = BASE
            else:
                self.output(failing=True)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.fixture.prepare()
            self.assertNotIn("e04-images-intent.json", self.journal.records())
        self.assert_no_delete()

    def test_duplicate_prepare_submission_and_closed_transaction_are_rejected(self):
        self.fixture.prepare()
        with self.assertRaisesRegex(ValueError, "already attempted"):
            self.fixture.prepare()
        self.fixture.start()
        with self.assertRaisesRegex(ValueError, "already attempted"):
            self.fixture.start()
        self.fixture.response(200, FAILURE)
        self.fixture.cleanup()
        with self.assertRaisesRegex(ValueError, "closed"):
            self.fixture.start(failing=True)
        with self.assertRaisesRegex(ValueError, "open submitted"):
            self.fixture.response(200, FAILURE)

    def test_output_appearing_after_prepare_is_not_adopted(self):
        self.fixture.prepare()
        self.output()
        with self.assertRaisesRegex(ValueError, "appeared"):
            self.fixture.start()
        with self.assertRaisesRegex(ValueError, "Unsubmitted"):
            self.fixture.cleanup()
        self.assert_no_delete()

    def test_unknown_completion_preserves_even_an_absent_output(self):
        self.fixture.prepare()
        self.fixture.start()
        recovered = self.reopen()
        with self.assertRaisesRegex(ValueError, "uncertain"):
            recovered.cleanup()
        self.output()
        recovered = self.reopen()
        with self.assertRaisesRegex(ValueError, "uncertain"):
            recovered.cleanup()
        self.assert_no_delete()

    def test_response_requires_submission_and_rejection_is_not_completion(self):
        self.fixture.prepare()
        with self.assertRaisesRegex(ValueError, "submitted"):
            self.fixture.response(200, SUCCESS)
        self.fixture.start()
        for status, payload in ((500, b'{"message":"builder unavailable"}'), (200, b'broken')):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.fixture.response(status, payload)
            self.assertNotIn("e04-built-completed.json", self.journal.records())
        self.assert_no_delete()

    def test_completed_response_cannot_be_replayed(self):
        self.submitted()
        with self.assertRaisesRegex(ValueError, "already recorded"):
            self.fixture.response(200, SUCCESS)

    def test_completion_survives_interrupted_identity_observation(self):
        self.fixture.prepare()
        self.fixture.start()
        self.output()
        with patch.object(self.fixture, "inspect", side_effect=TimeoutError("lost inspection")), self.assertRaises(TimeoutError):
            self.fixture.response(200, SUCCESS)
        self.assertIn("e04-built-completed.json", self.journal.records())
        self.assertNotIn("e04-built-created.json", self.journal.records())
        self.reopen().cleanup()
        self.assertEqual(list(self.server.images), [BASE])

    def test_cleanup_without_any_submission_only_closes_empty_output_names(self):
        self.fixture.prepare()
        self.fixture.cleanup()
        self.assert_no_delete()
        self.assertIn("e04-images-removed.json", self.journal.records())

    def test_recovery_plan_is_read_only_and_authenticates_completed_response(self):
        self.submitted()
        records = self.journal.records()
        self.assertEqual(self.fixture.recovery_plan(), [self.fixture.identity(self.output(), False)])
        self.assertEqual(records, self.journal.records())
        self.assert_no_delete()
        for name, payload in (("e04-built-response.jsonl", b'{}\n'),
                              ("e04-built-completed.json", b'{}'),
                              ("e04-built-started.json", b'{}')):
            with patch.object(self.fixture.journal, "records", return_value={**records, name: payload}), self.assertRaises(ValueError):
                self.fixture.recovery_plan()
        for omitted in ("e04-built-completed.json", "e04-built-started.json"):
            with patch.object(self.fixture.journal, "records", return_value={k: v for k, v in records.items() if k != omitted}), \
                    self.assertRaises(ValueError):
                self.fixture.recovery_plan()

    def intermediate_build(self):
        self.fixture.prepare()
        self.fixture.start()
        self.output()
        parent = self.image
        for token in ('e', 'f'):
            identifier = 'sha256:' + token * 64
            self.server.images[identifier] = {'Id': identifier, 'RepoTags': [], 'RepoDigests': [],
                'Containers': 0, 'Parent': parent, 'ParentId': parent, 'Descriptor': {'digest': identifier},
                'Config': {'Labels': {OWNER_LABEL: OWNER}}}
            parent = identifier
        payload = b'\n \n{"stream":" ---> eeeeeeeeeeee\\n"}\n\n{"stream":" ---> ffffffffffff\\n"}\n\n'
        self.fixture.response(200, payload)

    def test_classic_intermediates_are_reported_read_only_and_removed_child_first(self):
        self.intermediate_build()
        before = self.journal.records()
        plan = self.fixture.intermediate_plan()
        self.assertEqual([value['id'] for value in plan], ['sha256:' + 'f' * 64, 'sha256:' + 'e' * 64])
        self.assertEqual(self.journal.records(), before)
        self.assert_no_delete()
        self.fixture.cleanup()
        self.assertEqual(list(self.server.images), [BASE])
        deletes = [route for method, route in self.server.routes if method == 'DELETE']
        self.assertEqual(len(deletes), 3)
        self.assertIn('f' * 64, deletes[1])
        self.assertIn('e' * 64, deletes[2])
        self.reopen().cleanup()

    def test_unverified_shared_or_used_intermediate_is_not_removed(self):
        self.intermediate_build()
        identifier = 'sha256:' + 'f' * 64
        original = copy.deepcopy(self.server.images[identifier])
        for field, value in (('RepoTags', ['foreign:tag']), ('RepoDigests', [BASE]), ('Containers', 1),
                             ('Descriptor', {'digest': self.image}), ('Parent', 'changed')):
            self.server.images[identifier] = {**original, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.fixture.intermediate_plan()
            self.assert_no_delete()
        self.server.images[identifier] = original
        original_inspect = self.fixture.inspect
        with patch.object(self.fixture, 'inspect', side_effect=lambda reference: (
                {**original, 'Config': {'Labels': {OWNER_LABEL: 'foreign'}}} if reference == identifier
                else original_inspect(reference))), self.assertRaisesRegex(ValueError, 'identity changed'):
            self.fixture.intermediate_plan()
        with patch.object(self.fixture, 'inspect', return_value=None), self.assertRaises(ValueError):
            self.fixture.intermediate_plan()

    def test_intermediate_lost_delete_response_resumes_without_rebuilding(self):
        self.intermediate_build()
        original = self.client.call

        def interrupted(method, route, *args, **kwargs):
            result = original(method, route, *args, **kwargs)
            if method == 'DELETE' and 'f' * 64 in route:
                raise TimeoutError('lost intermediate deletion response')
            return result

        with patch.object(self.client, 'call', side_effect=interrupted), self.assertRaises(TimeoutError):
            self.fixture.cleanup()
        self.reopen().cleanup()
        self.assertEqual(len(self.server.delete_records), 3)
        self.assertEqual(list(self.server.images), [BASE])

    def test_context_or_runtime_mismatch_rejected_before_requests(self):
        self.fixture.prepare()
        before = len(self.server.routes)
        for field in ("owner", "base", "socket", "apiVersion", "image", "contexts"):
            fixture = self.reopen()
            fixture.intent[field] = "changed"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "another transaction"):
                fixture.cleanup()
        self.assertEqual(len(self.server.routes), before)

    def test_replaced_foreign_shared_and_digest_referenced_outputs_are_preserved(self):
        self.submitted()
        original = copy.deepcopy(self.server.images[tag_for(OWNER)])
        for field, value in (("Id", "sha256:" + "e" * 64),
                             ("Id", self.image),
                             ("RepoTags", [tag_for(OWNER), "user:latest"]),
                             ("Config", {"Labels": {OWNER_LABEL: "foreign"}}),
                             ("RepoDigests", [BASE])):
            changed = copy.deepcopy(original)
            changed[field] = value
            self.server.images[tag_for(OWNER)] = changed
            recovered = self.reopen()
            with self.subTest(field=field), self.assertRaises(ValueError):
                recovered.cleanup()
        self.assert_no_delete()

    def test_lost_delete_response_is_reconciled_without_rebuilding(self):
        self.submitted()
        original = self.client.call

        def interrupted(method, route, *args, **kwargs):
            result = original(method, route, *args, **kwargs)
            if method == "DELETE":
                raise TimeoutError("response lost after deletion")
            return result

        with patch.object(self.client, "call", side_effect=interrupted), self.assertRaises(TimeoutError):
            self.fixture.cleanup()
        self.assertIn("e04-built-delete.json", self.journal.records())
        self.reopen().cleanup()
        self.assertIn("e04-images-removed.json", self.journal.records())
        self.assertEqual(len(self.server.delete_records), 1)

    def test_ignored_delete_does_not_claim_cleanup(self):
        self.submitted()
        self.server.ignore_delete = True
        with self.assertRaisesRegex(ValueError, "unverified"):
            self.fixture.cleanup()
        self.assertNotIn("e04-images-removed.json", self.journal.records())

    def test_conflicting_creation_and_deletion_journals_preserve_the_image(self):
        self.submitted()
        self.journal.put("e04-built-delete.json", b'{"id":"sha256:other"}')
        recovered = self.reopen()
        with self.assertRaisesRegex(ValueError, "identities disagree"):
            recovered.cleanup()
        self.assert_no_delete()

    def test_id_inspection_must_agree_with_tag_before_deletion(self):
        self.submitted()
        original = self.fixture.inspect
        identifier = self.server.images[tag_for(OWNER)]["Id"]

        def missing_id(reference):
            return None if reference == identifier else original(reference)

        with patch.object(self.fixture, "inspect", side_effect=missing_id), self.assertRaisesRegex(ValueError, "ID disagree"):
            self.fixture.cleanup()
        self.assert_no_delete()

    def test_tag_loss_does_not_hide_remaining_image(self):
        self.submitted()
        image = self.server.images.pop(tag_for(OWNER))
        image["RepoTags"] = []
        self.server.images["untagged"] = image
        recovered = self.reopen()
        with self.assertRaisesRegex(ValueError, "lost its tag"):
            recovered.cleanup()
        self.assert_no_delete()

    def test_intermediate_residue_blocks_cleanup_without_broad_pruning(self):
        self.submitted()
        self.server.images["intermediate"] = {"Id": "sha256:" + "f" * 64,
                                               "Config": {"Labels": {OWNER_LABEL: OWNER}}, "RepoTags": []}
        with self.assertRaisesRegex(ValueError, "residue remains"):
            self.fixture.cleanup()
        self.assertIn("intermediate", self.server.images)
        self.assertNotIn("e04-images-removed.json", self.journal.records())
        self.assertEqual(len(self.server.delete_records), 1)
        recovered = self.reopen()
        with self.assertRaisesRegex(ValueError, "closed"):
            recovered.start(failing=True)
        self.assertNotIn("e04-failed-started.json", self.journal.records())
        self.output()
        recovered = self.reopen()
        with self.assertRaisesRegex(ValueError, "removed build output"):
            recovered.cleanup()
        self.assertEqual(len(self.server.delete_records), 1)

    def test_closed_transaction_reappearing_output_is_never_deleted(self):
        self.submitted()
        self.fixture.cleanup()
        self.output()
        recovered = self.reopen()
        with self.assertRaisesRegex(ValueError, "removed build output"):
            recovered.cleanup()
        self.assertEqual(len(self.server.delete_records), 1)

    def test_lost_delete_then_residue_does_not_allow_output_readoption(self):
        self.submitted()
        self.journal.put("e04-built-delete.json", self.journal.records()["e04-built-created.json"])
        del self.server.images[tag_for(OWNER)]
        self.server.images["intermediate"] = {"Id": "sha256:" + "f" * 64,
                                               "Config": {"Labels": {OWNER_LABEL: OWNER}}, "RepoTags": []}
        with self.assertRaisesRegex(ValueError, "residue"):
            self.fixture.cleanup()
        self.output()
        recovered = self.reopen()
        with self.assertRaisesRegex(ValueError, "removed build output"):
            recovered.cleanup()
        self.assert_no_delete()


if __name__ == "__main__":
    unittest.main()
