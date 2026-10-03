"""Known byte vectors for the pinned log driver, separate from socket fakes."""

import unittest

from json_file_oracle import encoded_record, history_bytes, RECORD_BYTES, REPLACEMENT


class JsonFileOracleTests(unittest.TestCase):
    def test_control_and_valid_unicode_are_preserved(self):
        value = bytes(range(128)) + "\u20ac\U0001f642\u2028\u2029".encode()
        self.assertEqual(history_bytes(value), value)

    def test_invalid_bytes_are_replaced_individually_not_as_one_subpart(self):
        self.assertEqual(encoded_record(b"\xe2\x82"), REPLACEMENT * 2)
        self.assertEqual(encoded_record(b"\xf0\x90\x80"), REPLACEMENT * 3)
        self.assertEqual(encoded_record(b"\xc0\xaf\xed\xa0\x80\xf4\x90\x80\x80"), REPLACEMENT * 9)

    def test_record_boundary_splits_otherwise_valid_utf8(self):
        prefix = b"a" * (RECORD_BYTES - 1)
        self.assertEqual(history_bytes(prefix + b"\xe2\x82\xac"), prefix + REPLACEMENT * 3)

    def test_newline_resets_record_capacity_and_preserves_eof_without_newline(self):
        prefix = b"a" * (RECORD_BYTES - 2) + b"\n"
        self.assertEqual(history_bytes(prefix + b"\xe2\x82\xac"), prefix + b"\xe2\x82\xac")
        self.assertEqual(history_bytes(b"unterminated"), b"unterminated")
        self.assertEqual(history_bytes(b""), b"")

    def test_generations_cannot_complete_each_others_partial_character(self):
        self.assertEqual(history_bytes(b"\xe2") + history_bytes(b"\x82\xac"), REPLACEMENT * 3)
        self.assertNotEqual(history_bytes(b"\xe2\x82\xac"), REPLACEMENT * 3)
