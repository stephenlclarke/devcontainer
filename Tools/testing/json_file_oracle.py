"""Pinned Moby json-file record semantics; never transform candidate output.

Moby 568f755: daemon/logger/copier.go and
daemon/logger/jsonfilelog/jsonlog/jsonlogbytes.go. The former emits at LF,
16 KiB or natural EOF; the latter replaces each invalid UTF-8 byte separately.
This independent expected-value model accepts complete per-source generations.
"""

REPLACEMENT = b"\xef\xbf\xbd"
RECORD_BYTES = 16 * 1024


def encoded_record(raw: bytes) -> bytes:
    result = bytearray()
    index = 0
    while index < len(raw):
        first = raw[index]
        width = 1 if first < 0x80 else 2 if 0xC2 <= first <= 0xDF else (
            3 if 0xE0 <= first <= 0xEF else 4 if 0xF0 <= first <= 0xF4 else 0)
        value = raw[index:index + width]
        valid = width != 0 and len(value) == width
        if valid:
            try:
                value.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                valid = False
        if valid:
            result.extend(value)
            index += width
        else:
            result.extend(REPLACEMENT)
            index += 1
    return bytes(result)


def history_bytes(raw: bytes) -> bytes:
    result = bytearray()
    offset = 0
    while offset < len(raw):
        limit = min(offset + RECORD_BYTES, len(raw))
        newline = raw.find(b"\n", offset, limit)
        end = newline + 1 if newline >= 0 else limit
        result.extend(encoded_record(raw[offset:end]))
        offset = end
    return bytes(result)
