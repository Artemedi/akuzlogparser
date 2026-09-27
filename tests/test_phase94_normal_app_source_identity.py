"""Phase 9.4 normal-app source-identity/cache gates; synthetic data only.

This is the real app's perform_build_current path with local snapshots and
SQLite/JS analytics; no SSH, user .log, credentials or persistent sidecars.
"""
from __future__ import annotations

import os
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
import unittest

from akuz_store import load_store, sha256
from scripts.bench_phase9_baseline import create_sources, inventory_manifest
from scripts.phase9_semantic import semantic_exports, semantic_sql
from tests.test_phase9_spool import build


def signatures(root: Path):
    return inventory_manifest(root), semantic_sql(root), semantic_exports(root)


def no_ephemeral_spools(root: Path):
    return not list((root / "cache").glob("akuz-phase9-derived-*"))


class NormalAppSourceIdentityTests(unittest.TestCase):
    def test_same_bytes_different_path_not_silently_deduplicated_on_restart(self):
        with TemporaryDirectory(prefix="akuz_phase94_source_identity_") as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 5, 96)
            first = sources / "20260924_A.log"
            alias = sources / "20260927_independent.log"
            copyfile(first, alias)
            self.assertNotEqual(first.resolve(), alias.resolve())
            self.assertEqual(sha256(first), sha256(alias))
            control = home / "control"
            current = home / "spool"
            expected, _ = build(control, sources, False)
            result, _ = build(current, sources, True)
            self.assertFalse(expected["reused"])
            self.assertFalse(result["reused"])
            self.assertEqual(len(result["reports"]), 4)
            self.assertEqual(len({r["id"] for r in result["reports"]}), 4)
            self.assertIsNotNone(result["combined"])
            self.assertFalse(result["combined"]["reused"])
            self.assertEqual(len(load_store(current)["downloads"]), 4)
            self.assertEqual(signatures(control), signatures(current))
            self.assertTrue(no_ephemeral_spools(current))
            # A newly constructed app State must reuse the on-disk index.
            previous = signatures(current)
            reopened, _ = build(current, sources, True)
            self.assertTrue(reopened["reused"])
            self.assertTrue(all(r["reused"] for r in reopened["reports"]))
            self.assertTrue(reopened["combined"]["reused"])
            self.assertEqual(previous, signatures(current))
            self.assertEqual(len(load_store(current)["reports"]), 5)
            self.assertTrue(no_ephemeral_spools(current))

    def test_renamed_source_is_not_mistaken_for_old_cached_identity(self):
        with TemporaryDirectory(prefix="akuz_phase94_rename_") as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 5, 96)
            root = home / "spool"
            old, _ = build(root, sources, True)
            self.assertFalse(old["reused"])
            original_ids = {r["id"] for r in old["reports"]}
            old_combined_id = old["combined"]["id"]
            source = sources / "20260924_A.log"
            new_path = sources / "20260924_renamed.log"
            expected_sha = sha256(source)
            source.rename(new_path)
            self.assertEqual(sha256(new_path), expected_sha)
            changed, _ = build(root, sources, True)
            self.assertFalse(changed["reused"])
            self.assertEqual([r["reused"] for r in changed["reports"]].count(False), 1)
            self.assertEqual([r["reused"] for r in changed["reports"]].count(True), 2)
            self.assertFalse(changed["combined"]["reused"])
            self.assertNotEqual(old_combined_id, changed["combined"]["id"])
            self.assertTrue(original_ids.issubset(set(load_store(root)["reports"])))
            self.assertEqual(len(load_store(root)["reports"]), 6)
            self.assertTrue(no_ephemeral_spools(root))
            warm, _ = build(root, sources, True)
            self.assertTrue(warm["reused"])
            self.assertEqual(len(load_store(root)["reports"]), 6)

    def test_same_size_changed_contents_with_new_mtime_invalidates_report(self):
        with TemporaryDirectory(prefix="akuz_phase94_same_size_") as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 5, 96)
            root = home / "spool"
            before, _ = build(root, sources, True)
            original_ids = {r["id"] for r in before["reports"]}
            original_combined = before["combined"]["id"]
            source = sources / "20260924_A.log"
            stat = source.stat()
            contents = source.read_bytes()
            self.assertIn(b"synthetic event", contents)
            replacement = contents.replace(b"synthetic event",
                                           b"synthetix event", 1)
            self.assertEqual(len(contents), len(replacement))
            source.write_bytes(replacement)
            os.utime(source, ns=(stat.st_atime_ns,
                                 stat.st_mtime_ns + 1_000_000_000))
            self.assertEqual(source.stat().st_size, stat.st_size)
            self.assertNotEqual(sha256(source),
                                __import__("hashlib").sha256(contents).hexdigest())
            newer, _ = build(root, sources, True)
            self.assertFalse(newer["reused"])
            self.assertEqual(sum(not x["reused"] for x in newer["reports"]), 1)
            self.assertFalse(newer["combined"]["reused"])
            self.assertNotEqual(newer["combined"]["id"], original_combined)
            self.assertTrue(original_ids.issubset(set(load_store(root)["reports"])))
            self.assertEqual(len(load_store(root)["reports"]), 6)
            self.assertTrue(no_ephemeral_spools(root))
            self.assertTrue(build(root, sources, True)[0]["reused"])


if __name__ == "__main__":
    unittest.main()
