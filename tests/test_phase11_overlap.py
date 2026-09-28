"""Phase 11 isolated one-ahead scheduler contract tests."""
from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from scripts.phase11_overlap import (
    CompletedSnapshot, run_one_ahead, run_serial)


class Item:
    def __init__(self, name):
        self.name = name


class Phase11OverlapTests(unittest.TestCase):
    def snapshot(self, root: Path, item: Item, payload: bytes | None = None):
        data = payload if payload is not None else item.name.encode("ascii")
        path = root / (item.name + ".log")
        path.write_bytes(data)
        return CompletedSnapshot(
            item=item, path=path,
            digest=hashlib.sha256(data).hexdigest(), bytes=len(data))

    def test_one_ahead_starts_next_fetch_during_current_parse(self):
        with TemporaryDirectory(prefix="akuz_phase11_overlap_") as td:
            root = Path(td)
            a, b = Item("a"), Item("b")
            fetch_b_started = threading.Event()
            allow_b_finish = threading.Event()
            seen = []

            def fetch(item):
                if item is a:
                    seen.append("fetch-a-done")
                    return self.snapshot(root, item)
                self.assertIs(item, b)
                seen.append("fetch-b-start")
                fetch_b_started.set()
                self.assertTrue(allow_b_finish.wait(3))
                seen.append("fetch-b-done")
                return self.snapshot(root, item)

            def parse(snapshot):
                if snapshot.item is a:
                    seen.append("parse-a-start")
                    self.assertTrue(fetch_b_started.wait(3),
                                    "next fetch did not overlap current parse")
                    self.assertTrue(snapshot.path.is_file())
                    allow_b_finish.set()
                    # Keep parse A alive until B fetch has a chance to finish.
                    time.sleep(.02)
                    seen.append("parse-a-done")
                    return "parsed-a"
                self.assertIs(snapshot.item, b)
                seen.append("parse-b")
                return "parsed-b"

            result = run_one_ahead([a, b], fetch, parse)
            self.assertEqual(result.outputs, ("parsed-a", "parsed-b"))
            self.assertEqual(result.fetched, 2)
            self.assertEqual(result.parsed, 2)
            self.assertLess(seen.index("fetch-b-start"), seen.index("parse-a-done"))
            self.assertLess(seen.index("parse-a-start"), seen.index("fetch-b-done"))

    def test_parser_sees_only_completed_snapshot_not_part_file(self):
        with TemporaryDirectory(prefix="akuz_phase11_complete_") as td:
            root = Path(td)
            item = Item("only")
            def fetch(value):
                part = root / "only.log.part"
                final = root / "only.log"
                part.write_bytes(b"complete\n")
                part.replace(final)
                data = final.read_bytes()
                return CompletedSnapshot(
                    item=value, path=final,
                    digest=hashlib.sha256(data).hexdigest(),
                    bytes=len(data))
            def parse(snapshot):
                self.assertTrue(snapshot.path.is_file())
                self.assertFalse((root / "only.log.part").exists())
                self.assertEqual(snapshot.path.read_bytes(), b"complete\n")
                return "ok"
            self.assertEqual(run_one_ahead([item], fetch, parse).outputs, ("ok",))

    def test_fetch_failure_stops_before_later_source_and_preserves_prior_parse(self):
        with TemporaryDirectory(prefix="akuz_phase11_fetch_fail_") as td:
            root = Path(td)
            a, b, c = Item("a"), Item("b"), Item("c")
            fetched, parsed = [], []
            def fetch(item):
                fetched.append(item.name)
                if item is b:
                    raise RuntimeError("injected fetch failure")
                return self.snapshot(root, item)
            def parse(snapshot):
                parsed.append(snapshot.item.name)
                return snapshot.item.name
            with self.assertRaisesRegex(RuntimeError, "fetch failure"):
                run_one_ahead([a, b, c], fetch, parse)
            self.assertEqual(fetched, ["a", "b"])
            self.assertEqual(parsed, ["a"])
            self.assertFalse((root / "c.log").exists())

    def test_parse_failure_joins_inflight_fetch_but_never_parses_it(self):
        with TemporaryDirectory(prefix="akuz_phase11_parse_fail_") as td:
            root = Path(td)
            a, b, c = Item("a"), Item("b"), Item("c")
            b_started = threading.Event()
            b_finished = threading.Event()
            parsed = []
            def fetch(item):
                if item is b:
                    b_started.set()
                    time.sleep(.03)
                    snap = self.snapshot(root, item)
                    b_finished.set()
                    return snap
                return self.snapshot(root, item)
            def parse(snapshot):
                parsed.append(snapshot.item.name)
                if snapshot.item is a:
                    self.assertTrue(b_started.wait(3))
                    raise ValueError("injected parse failure")
                return snapshot.item.name
            with self.assertRaisesRegex(ValueError, "parse failure"):
                run_one_ahead([a, b, c], fetch, parse)
            self.assertTrue(b_finished.is_set(),
                            "scheduler must join an already-running fetch")
            self.assertEqual(parsed, ["a"])
            self.assertFalse((root / "c.log").exists())

    def test_order_matches_serial_reference(self):
        with TemporaryDirectory(prefix="akuz_phase11_order_") as td:
            root = Path(td)
            items = [Item("a"), Item("b"), Item("c")]
            def fetch(item):
                return self.snapshot(root, item)
            def parse(snapshot):
                return (snapshot.item.name, snapshot.digest)
            serial = run_serial(items, fetch, parse)
            overlapped = run_one_ahead(items, fetch, parse)
            self.assertEqual(overlapped.outputs, serial.outputs)
            self.assertEqual(overlapped.fetched, 3)
            self.assertEqual(overlapped.parsed, 3)

    def test_invalid_snapshot_contract_fails_closed(self):
        item = Item("a")
        with self.assertRaisesRegex(TypeError, "CompletedSnapshot"):
            run_one_ahead([item], lambda _: object(), lambda _: None)
        with TemporaryDirectory(prefix="akuz_phase11_bad_") as td:
            root = Path(td)
            other = Item("other")
            path = root / "a.log"
            path.write_bytes(b"a")
            bad = CompletedSnapshot(other, path, "a" * 64, 1)
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                run_one_ahead([item], lambda _: bad, lambda _: None)

    def test_empty_input_is_noop(self):
        result = run_one_ahead([], lambda _: None, lambda _: None)
        self.assertEqual(result.outputs, ())
        self.assertEqual(result.fetched, 0)
        self.assertEqual(result.parsed, 0)


if __name__ == "__main__":
    unittest.main()
