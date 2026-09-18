"""E04 deterministic inputs and hostile stream/ownership cases, without a builder."""

import io
import json
import tarfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from build_probe import build_context, build_output, build_route, owned_image, tag_for
from guest_fixture import OWNER_LABEL


OWNER = "a" * 64
BASE = "docker.io/library/alpine@sha256:" + "b" * 64


class BuildProbeTests(unittest.TestCase):
    def test_context_retains_original_run_buildarg_label_and_failure(self):
        for failing in (False, True):
            payload = build_context(BASE, OWNER, failing=failing)
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
                self.assertEqual(archive.getnames(), ["Dockerfile"])
                member = archive.getmember("Dockerfile")
                self.assertEqual((member.mode, member.uid, member.gid, member.mtime), (0o644, 0, 0, 0))
                content = archive.extractfile(member).read().decode()
                self.assertIn("FROM " + BASE + "\n", content)
                self.assertIn(f'LABEL {OWNER_LABEL}="{OWNER}"\n', content)
                self.assertIn("ARG PARITY_VALUE\n", content)
                if failing:
                    self.assertIn("RUN false\n", content)
                    self.assertNotIn('devcontainer.parity="true"', content)
                else:
                    self.assertIn('RUN test "$PARITY_VALUE" = expected\n', content)
                    self.assertIn('LABEL devcontainer.parity="true"\n', content)
                    self.assertIn('CMD ["true"]\n', content)

    def test_routes_separate_tags_and_encode_buildargs_without_forcing_pulls(self):
        for failing in (False, True):
            route = urlsplit(build_route(OWNER, failing=failing))
            self.assertEqual(route.path, "/build")
            query = parse_qs(route.query)
            self.assertEqual(query["t"], [tag_for(OWNER, failing=failing)])
            self.assertEqual(query["dockerfile"], ["Dockerfile"])
            self.assertEqual(json.loads(query["buildargs"][0]), {"PARITY_VALUE": "expected"})
            self.assertEqual(query["pull"], ["false"])
            self.assertEqual(query["rm"], ["true"])
            self.assertEqual(query["forcerm"], ["true"])
        self.assertNotEqual(tag_for(OWNER), tag_for(OWNER, failing=True))

    def test_mutable_bases_and_dockerfile_injection_are_rejected(self):
        for base in ("alpine:latest", "scratch", BASE + "\nRUN true", "../alpine@sha256:" + "b" * 64):
            with self.subTest(base=base), self.assertRaisesRegex(ValueError, "digest-pinned"):
                build_context(base, OWNER)
        for owner in ("", "a" * 63, "A" * 64, "../escape", None):
            with self.subTest(owner=owner), self.assertRaisesRegex(ValueError, "owner"):
                build_route(owner)

    def test_json_stream_preserves_failure_even_after_later_progress(self):
        payload = b'{"stream":"Step 1/2"}\n{"errorDetail":{"message":"RUN failed","code":1}}\n{"status":"done"}\n'
        result = build_output(200, payload)
        self.assertTrue(result.progress)
        self.assertTrue(result.failed)
        self.assertEqual(result.records, 3)
        self.assertTrue(build_output(200, b'{"error":"build failed"}\n').failed)

    def test_aux_only_is_not_progress_and_blank_lines_are_not_records(self):
        result = build_output(200, b'\n{"aux":{"ID":"sha256:abc"}}\r\n\n')
        self.assertFalse(result.progress)
        self.assertFalse(result.failed)
        self.assertEqual(result.records, 1)
        result = build_output(200, b'{"stream":"Step 1"}\n{"aux":{"ID":"sha256:abc"}}\n')
        self.assertTrue(result.progress)
        self.assertFalse(result.failed)

    def test_http_rejection_cannot_count_as_expected_failed_run(self):
        for status in (400, 404, 500):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "rejected"):
                build_output(status, b'{"message":"builder unavailable"}')

    def test_malformed_duplicate_and_bounded_streams_fail_closed(self):
        for payload in (b"", b" \n", b"[]", b"{}", b"not-json", b'{"stream":1}',
                        b'{"stream":"good","stream":"duplicate"}', b'{"error":""}',
                        b'{"errorDetail":[]}', b'{"errorDetail":{"message":1}}',
                        b'{"error":"bad","errorDetail":{"message":""}}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                build_output(200, payload)
        # A later malformed record must not be hidden by a latched true result.
        for payload in (b'{"stream":"ok"}\n{"status":1}', b'{"error":"bad"}\n{"errorDetail":[]}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                build_output(200, payload)
        with patch("build_probe.MAX_BUILD_OUTPUT", 4), self.assertRaisesRegex(ValueError, "bound"):
            build_output(200, b'{"stream":"long"}')

    def image(self):
        return {"Id": "sha256:" + "c" * 64, "RepoTags": [tag_for(OWNER)],
                "Config": {"Labels": {OWNER_LABEL: OWNER, "devcontainer.parity": "true"}}}

    def test_identity_requires_full_id_exact_owner_and_no_foreign_tags(self):
        value = self.image()
        self.assertEqual(owned_image(value, OWNER),
                         {"id": value["Id"], "tag": tag_for(OWNER), "owner": OWNER, "inspect_label": True})
        for changed in (dict(value, Id="short"), dict(value, RepoTags=[]),
                        dict(value, RepoTags=[tag_for(OWNER), "operator:latest"]),
                        dict(value, Config=None), dict(value, Config={"Labels": {OWNER_LABEL: "other"}})):
            with self.subTest(image=changed), self.assertRaisesRegex(ValueError, "ownership"):
                owned_image(changed, OWNER)
        value["Config"]["Labels"]["devcontainer.parity"] = "false"
        self.assertFalse(owned_image(value, OWNER)["inspect_label"])
        value["RepoTags"] = [tag_for(OWNER, failing=True)]
        self.assertEqual(owned_image(value, OWNER, failing=True)["tag"], value["RepoTags"][0])


if __name__ == "__main__":
    unittest.main()
