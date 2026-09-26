"""Experimental Phase 9 derived spool; private, temporary, and source-local.

Only the current build owns these files. Never publish, cache persistently,
or expose via the localhost report server: headlines may contain patient data.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Iterator

from akuz_derived import DerivedEvent


def encode(ev: dict, derived: DerivedEvent) -> str:
    """Preserve immutable, context-free values and original event coordinates."""
    return json.dumps([
        ev["event_id"], ev["start_line"], ev["end_line"],
        derived.category, derived.normalized_pattern,
        derived.duration, derived.error_fp, derived.replacement_chars,
        derived.headline,
    ], ensure_ascii=False, separators=(",", ":")) + "\n"


class SpoolWriter:
    def __init__(self, path: Path):
        self.path = path
        self.count = 0
        self.bytes_written = 0
        self._digest = hashlib.sha256()
        self._handle = None

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    def __enter__(self):
        self._handle = self.path.open("x", encoding="utf-8", newline="\n")
        return self

    def __call__(self, ev: dict, value: DerivedEvent):
        if self._handle is None:
            raise RuntimeError("Derived spool is not open")
        if ev["event_id"] != self.count + 1:
            raise ValueError("Non-sequential derived spool event ID")
        line = encode(ev, value)
        self._handle.write(line)
        encoded = line.encode("utf-8")
        self._digest.update(encoded)
        self.count += 1
        self.bytes_written += len(encoded)

    def __exit__(self, exc_type, exc, tb):
        if self._handle is not None:
            self._handle.close()
        self._handle = None


@contextmanager
def replay(path: Path) -> Iterator[Iterator[tuple[int, int, int, DerivedEvent]]]:
    """Close file even if the combined writer aborts half-way through."""
    with path.open("r", encoding="utf-8", newline="\n") as handle:
        def records():
            for text in handle:
                row = json.loads(text)
                if not isinstance(row, list) or len(row) != 9:
                    raise ValueError("Unexpected derived spool record")
                eid, start, end, category, pattern, duration, fp, count, headline = row
                if duration is not None:
                    duration = tuple(duration)
                derived = DerivedEvent(
                    category, pattern, duration, fp, count, headline,
                    0.0, 0.0, 0.0, 0.0, 0.0)
                yield eid, start, end, derived
        yield records()


def verified_next(items, ev: dict) -> DerivedEvent:
    try:
        eid, start, end, derived = next(items)
    except StopIteration as exc:
        raise ValueError("Derived spool ended before event stream") from exc
    if (eid, start, end) != (
            ev["event_id"], ev["start_line"], ev["end_line"]):
        raise ValueError("Derived spool source coordinates mismatch")
    return derived


def verify_exhausted(items):
    try:
        next(items)
    except StopIteration:
        return
    raise ValueError("Derived spool has trailing events")
