"""Byte-equivalent UTF-8 report writer with bounded producer-side SHA-256.

Replicates Path.write_text(..., encoding='utf-8') default native newline
translation; do not use for binary payloads or untrusted paths.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path

_CHARS = 262144


def write_report_text(path: Path, text: str) -> tuple[str, int]:
    """Write text once and return SHA-256 + actual byte count.

    Update the digest only after the write call accepts the bytes. Close
    errors are propagated; callers must not publish a partial report.
    """
    if not isinstance(text, str):
        raise TypeError('Report text must be a string')
    digest = hashlib.sha256()
    size = 0
    with path.open('wb') as output:
        for start in range(0, len(text), _CHARS):
            part = text[start:start + _CHARS]
            if os.linesep != '\n':
                part = part.replace('\n', os.linesep)
            payload = part.encode('utf-8')
            written = output.write(payload)
            if written != len(payload):
                raise OSError('Incomplete report write')
            digest.update(payload)
            size += written
    return digest.hexdigest(), size
