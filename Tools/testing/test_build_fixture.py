"""Exercise binary build submission and interruption through a real Unix socket."""

import io
import json
import tarfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from build_fixture import BuildFixture
from build_probe import build_context, failure_marker, tag_for
from case_evidence import canonical
from guest_fixture import OWNER_LABEL
import test_build_images as images


class Handler(images.Handler):
    def dispatch(self):
        url = urlsplit(self.path)
        if self.command != "POST" or url.path != "/v1.53/build":
            return super().dispatch()
        server = self.server
        tag = parse_qs(url.query)["t"][0]
        failing = tag == tag_for(images.OWNER, failing=True)
        body = self.rfile.read(int(self.headers["Content-Length"]))
        server.submissions.append({"body": body, "type": self.headers["Content-Type"],
                                   "query": parse_qs(url.query), "journal": server.journal.records()})
        if server.reject:
            return 500, {"message": "builder unavailable"}
        if not failing or server.failed_output:
            server.images[tag] = {"Id": "sha256:" + ("e" if failing else "d") * 64,
                                  "RepoTags": [tag], "RepoDigests": [],
                                  "Config": {"Labels": {OWNER_LABEL: images.OWNER,
                                                         "devcontainer.parity": server.label}}}
        if failing:
            stream = failure_marker(images.OWNER) if server.executed else "RUN false"
            return 200, canonical({"stream": stream + "\n"}) + b'\n' + canonical({"error": "exit code: 1"}) + b'\n'
        return 200, images.SUCCESS


class BuildFixtureTests(unittest.TestCase):
    stop = images.BuildImagesTests.stop
    reopen = images.BuildImagesTests.reopen

    def setUp(self):
        with patch.object(images, 'Handler', Handler):
            images.BuildImagesTests.setUp(self)
        self.server.submissions = []
        self.server.reject, self.server.failed_output = False, False
        self.server.executed, self.server.label = True, "true"
        self.events = []
        self.fixture = BuildFixture(self.socket, images.OWNER, self.image, '1.53', self.journal,
                                    images.BASE, observe=self.events.append)

    def test_real_binary_context_intent_failure_phase_and_cleanup(self):
        self.assertEqual(self.fixture.operation(), {'build_progress': 'true', 'failed_build': 'true', 'inspect_label': 'true'})
        for failing, submitted in zip((False, True), self.server.submissions):
            self.assertEqual(submitted['body'], build_context(images.BASE, images.OWNER, failing=failing))
            self.assertEqual(submitted['type'], 'application/x-tar')
            self.assertIn(self.fixture.images.key(failing, 'started'), submitted['journal'])
            with tarfile.open(fileobj=io.BytesIO(submitted['body']), mode='r:') as archive:
                self.assertEqual(archive.getnames(), ['Dockerfile'])
        posts = [event for event in self.events if event['method'] == 'POST']
        self.assertEqual(len(posts), 2)
        self.assertTrue(all(event['durationNS'] > 0 and event['status'] == 200 for event in posts))
        self.fixture.cleanup()
        self.assertEqual(list(self.server.images), [images.BASE])

    def test_preflight_rejection_is_not_expected_failure_or_safe_completion(self):
        self.server.reject = True
        with self.assertRaisesRegex(ValueError, 'rejected'):
            self.fixture.operation()
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            self.fixture.cleanup()
        self.assertFalse(self.server.delete_records)
        self.assertEqual(self.events[-1]['status'], 500)

    def test_wrong_failure_phase_stays_failed_but_completed_outputs_can_be_cleaned(self):
        self.server.executed = False
        with self.assertRaisesRegex(ValueError, 'intended RUN'):
            self.fixture.operation()
        self.fixture.cleanup()
        self.assertEqual(list(self.server.images), [images.BASE])

    def test_wrong_label_and_unexpected_failed_output_are_not_parity(self):
        self.server.label = 'false'
        with self.assertRaisesRegex(ValueError, 'expected identity and label'):
            self.fixture.operation()
        self.fixture.cleanup()

    def test_failed_build_must_not_publish_an_output(self):
        self.server.failed_output = True
        with self.assertRaisesRegex(ValueError, 'unexpectedly published'):
            self.fixture.operation()
        self.fixture.cleanup()
        self.assertEqual(list(self.server.images), [images.BASE])

    def test_timeout_records_timing_and_keeps_uncertain_runtime(self):
        with patch('build_fixture.request', side_effect=TimeoutError('fixture timeout')):
            with self.assertRaises(TimeoutError):
                self.fixture.operation()
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            self.fixture.cleanup()
        self.assertEqual(self.events[-1]['error'], 'TimeoutError')
        self.assertGreater(self.events[-1]['durationNS'], 0)

    def test_unsubmitted_cleanup_is_empty(self):
        self.assertEqual(self.fixture.cleanup()['remainingOwnedResources'], [])

    def test_each_submission_revalidates_its_private_builder_before_intent(self):
        checks = []
        def check():
            checks.append(len(self.server.submissions))
            if len(checks) == 2:
                raise ValueError('host DNS changed')
        self.fixture.before_submit = check
        with self.assertRaisesRegex(ValueError, 'host DNS changed'):
            self.fixture.operation()
        self.assertEqual(checks, [0, 1])
        self.assertNotIn('e04-failed-started.json', self.journal.records())
        self.fixture.cleanup()

    def test_positive_build_error_does_not_become_a_successful_result(self):
        with patch.object(images, 'SUCCESS', b'{"error":"builder failed"}\n'):
            with self.assertRaisesRegex(ValueError, 'successful progress'):
                self.fixture.operation()
        self.fixture.cleanup()

    def test_positive_build_without_progress_does_not_pass(self):
        with patch.object(images, 'SUCCESS', b'{"aux":{"ID":"sha256:abc"}}\n'):
            with self.assertRaisesRegex(ValueError, 'successful progress'):
                self.fixture.operation()
        self.fixture.cleanup()


if __name__ == '__main__':
    unittest.main()
