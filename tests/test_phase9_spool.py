"""Bounded Phase 9.3 prototype: no persistent sidecar and no user cache access."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from akuz_app import State, perform_build_current, perform_list
from akuz_derived import DerivedEvent
from akuz_derived_spool import SpoolWriter, replay, verified_next, verify_exhausted
from akuz_store import load_store, sha256
from scripts.bench_phase9_baseline import (
    canonical_hash, create_sources, inventory_manifest, remove_report)


def build(root: Path, sources: Path, optimized: bool):
    state = State()
    perform_list(root, state, source="local", local_path=str(sources))
    selected = [dict(id=row["id"], date="") for row in
                sorted(state.listing, key=lambda item: item["name"])]
    perform_build_current(root, state, selected, use_derived_spool=optimized)
    if state.result["analytics_warning"]:
        raise AssertionError("Synthetic analytics failed")
    inv = inventory_manifest(root)
    return state.result, inv


class SpoolPrototypeTests(unittest.TestCase):
    def test_codec_roundtrip_and_fail_closed_alignment(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "spool.jsonl"
            event = dict(event_id=1, start_line=6, end_line=8)
            value = DerivedEvent("class", "ä ☃\n x", (3.5, "ms"),
                                 "fingerprint", 2, "header", 1,2,3,4,5)
            with SpoolWriter(path) as sink:
                sink(event, value)
                self.assertEqual(sink.count, 1)
            self.assertEqual(sink.bytes_written, path.stat().st_size)
            self.assertEqual(sink.sha256, sha256(path))
            with replay(path) as rows:
                derived = verified_next(rows, event)
                self.assertEqual(replace(derived, folded_s=1, classify_s=2,
                    normalize_s=3, duration_s=4, error_s=5), value)
                verify_exhausted(rows)
            with replay(path) as rows:
                with self.assertRaisesRegex(ValueError, "coordinates"):
                    verified_next(rows, dict(event, start_line=7))
            with replay(path) as rows:
                with self.assertRaisesRegex(ValueError, "trailing"):
                    verify_exhausted(rows)
            with replay(path) as rows:
                verified_next(rows, event)
                with self.assertRaisesRegex(ValueError, "before"):
                    verified_next(rows, dict(event, event_id=2))

    def test_fresh_byte_equivalence_and_warm_reuse(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 15, 512)
            baseline, b = build(home/"reference", sources, False)
            candidate, c = build(home/"experimental", sources, True)
            self.assertFalse(baseline["reused"])
            self.assertFalse(candidate["reused"])
            self.assertEqual(b, c)
            self.assertEqual(sum(x["events"] for x in c["reports"].values()
                                 if x["kind"] == "single"), 46)
            self.assertFalse(list((home/"experimental"/"cache").glob(
                "akuz-phase9-derived-*")))
            self.assertEqual(len(load_store(home/"experimental")["reports"]),
                             len(load_store(home/"reference")["reports"]))
            warm, w = build(home/"experimental", sources, True)
            self.assertTrue(warm["reused"])
            self.assertEqual(c, w)

    def test_mixed_cache_only_new_source_uses_spool(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            sources = home/"sources"
            create_sources(sources, 12, 768)
            _, expected = build(home/"control", sources, False)
            _, first = build(home/"experimental", sources, True)
            self.assertEqual(first, expected)
            remove_report(home/"experimental", "single")
            remove_report(home/"experimental", "combined")
            rerun, updated = build(home/"experimental", sources, True)
            self.assertFalse(rerun["reused"])
            self.assertEqual(expected, updated)
            self.assertFalse(list((home/"experimental"/"cache").glob(
                "akuz-phase9-derived-*")))

    def test_spool_failure_does_not_publish_partial_report_or_spool(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 8, 512)
            original = SpoolWriter.__call__
            def fail(self, ev, derived):
                if self.count == 2:
                    raise OSError("injected spool disk-full")
                return original(self, ev, derived)
            with patch.object(SpoolWriter, "__call__", fail):
                with self.assertRaisesRegex(OSError, "spool disk-full"):
                    build(home/"experimental", sources, True)
            root = home/"experimental"
            self.assertFalse(list((root/"cache").glob(
                "akuz-phase9-derived-*")))
            self.assertFalse(list((root/"reports").glob("*.building")))
            self.assertEqual(len(load_store(root)["reports"]), 0)

    def test_spool_corruption_fails_closed_then_recovers_without_stale_cache(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 12, 768)
            _, expected = build(home / "control", sources, False)
            original_exit = SpoolWriter.__exit__
            def corrupt_after_close(self, exc_type, exc, tb):
                result = original_exit(self, exc_type, exc, tb)
                if self.path.name == "0000.jsonl" and exc is None:
                    with self.path.open("a", encoding="utf-8") as stream:
                        stream.write("[]\\n")
                return result
            with patch.object(SpoolWriter, "__exit__", corrupt_after_close):
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    build(home / "experimental", sources, True)
            root = home / "experimental"
            self.assertFalse(list((root / "cache").glob("akuz-phase9-derived-*")))
            self.assertFalse(list((root / "reports").glob("*.building")))
            store = load_store(root)
            self.assertEqual(len(store["reports"]), 3)
            self.assertTrue(all(row["kind"] == "single"
                                for row in store["reports"].values()))
            recovered, current = build(root, sources, True)
            self.assertFalse(recovered["reused"])
            self.assertEqual(current, expected)
            self.assertFalse(list((root / "cache").glob("akuz-phase9-derived-*")))


if __name__ == "__main__":
    unittest.main()
