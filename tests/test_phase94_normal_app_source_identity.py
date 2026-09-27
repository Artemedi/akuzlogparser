"""Phase 9.4 normal-app source-identity/cache gates; synthetic data only.

This is the real app's perform_build_current path with local snapshots and
SQLite/JS analytics; no SSH, user .log, credentials or persistent sidecars.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from akuz_store import load_store, sha256
from akuz_fetch import FetchError
from akuz_local import load_local_config
from akuz_store import clear_cache
from scripts.bench_phase9_baseline import create_sources, inventory_manifest
from scripts.phase9_semantic import semantic_exports, semantic_sql
from tests.test_phase9_spool import build

ROOT = Path(__file__).resolve().parents[1]


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

    def test_warm_inventory_and_analytics_survive_real_python_restart(self):
        with TemporaryDirectory(prefix="akuz_phase94_real_restart_") as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 5, 96)
            root = home / "spool"
            initial, _ = build(root, sources, True)
            self.assertFalse(initial["reused"])
            expected = signatures(root)
            report_ids = [r["id"] for r in initial["reports"]]
            combined_id = initial["combined"]["id"]
            worker = """
import json
import sys
from pathlib import Path
from akuz_store import load_store
from tests.test_phase9_spool import build
root, sources = map(Path, sys.argv[1:3])
result, _ = build(root, sources, True)
state = load_store(root)
print('RESTART_JSON=' + json.dumps(dict(
    reused=result['reused'],
    singles=[r['id'] for r in result['reports']],
    singles_reused=[r['reused'] for r in result['reports']],
    combined_id=result['combined']['id'],
    combined_reused=result['combined']['reused'],
    inventory_count=len(state['reports']),
    analytics_warning=result['analytics_warning'],
    lingering_spools=len(list((root/'cache').glob('akuz-phase9-derived-*'))),
), ensure_ascii=False))
"""
            child = subprocess.run(
                [sys.executable, "-B", "-c", worker, str(root), str(sources)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                env=dict(os.environ, PYTHONUTF8="1"), timeout=60)
            self.assertEqual(child.returncode, 0, child.stderr)
            marker = next((line for line in child.stdout.splitlines()
                           if line.startswith("RESTART_JSON=")), None)
            self.assertIsNotNone(marker, child.stdout)
            result = json.loads(marker.partition("=")[2])
            self.assertTrue(result["reused"])
            self.assertEqual(result["singles"], report_ids)
            self.assertEqual(result["singles_reused"], [True, True, True])
            self.assertEqual(result["combined_id"], combined_id)
            self.assertTrue(result["combined_reused"])
            self.assertEqual(result["inventory_count"], 4)
            self.assertEqual(result["analytics_warning"], "")
            self.assertEqual(result["lingering_spools"], 0)
            self.assertEqual(signatures(root), expected)

    def test_failed_combined_replay_then_new_interpreter_recovers_ready_singles(self):
        from unittest.mock import patch

        with TemporaryDirectory(prefix="akuz_phase94_fault_restart_") as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 5, 96)
            control = home / "control"
            build(control, sources, False)
            expected = signatures(control)
            root = home / "spool"
            with patch("akuz_app.verified_next",
                       side_effect=ValueError("injected replay failure")):
                with self.assertRaisesRegex(ValueError, "replay failure"):
                    build(root, sources, True)
            previous = load_store(root)
            single_ids = {row["id"] for row in previous["reports"].values()
                          if row["kind"] == "single"}
            self.assertEqual(len(single_ids), 3)
            self.assertEqual(len(previous["reports"]), 3)
            self.assertFalse(list((root / "reports").glob("*.building")))
            self.assertTrue(no_ephemeral_spools(root))
            worker = """
import json
import sys
from pathlib import Path
from akuz_store import load_store
from tests.test_phase9_spool import build
root, sources = map(Path, sys.argv[1:3])
result, _ = build(root, sources, True)
inventory = load_store(root)
print('RECOVERY_JSON=' + json.dumps(dict(
    singles=[r['id'] for r in result['reports']],
    single_reused=[r['reused'] for r in result['reports']],
    combined_reused=result['combined']['reused'],
    combined_id=result['combined']['id'],
    inventory_count=len(inventory['reports']),
    warning=result['analytics_warning'],
    lingering_spools=len(list((root/'cache').glob('akuz-phase9-derived-*'))),
)))
"""
            child = subprocess.run(
                [sys.executable, "-B", "-c", worker, str(root), str(sources)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                env=dict(os.environ, PYTHONUTF8="1"), timeout=60)
            self.assertEqual(child.returncode, 0, child.stderr)
            line = next((line for line in child.stdout.splitlines()
                         if line.startswith("RECOVERY_JSON=")), None)
            self.assertIsNotNone(line, child.stdout)
            value = json.loads(line.partition("=")[2])
            self.assertEqual(set(value["singles"]), single_ids)
            self.assertEqual(value["single_reused"], [True, True, True])
            self.assertFalse(value["combined_reused"])
            self.assertNotIn(value["combined_id"], single_ids)
            self.assertEqual(value["inventory_count"], 4)
            self.assertEqual(value["warning"], "")
            self.assertEqual(value["lingering_spools"], 0)
            self.assertEqual(signatures(root), expected)
            self.assertTrue(build(root, sources, True)[0]["reused"])

    def test_strict_local_origin_sha_detects_same_stat_rewrite(self):
        """Full SHA detects an in-place change invisible to the fast stat ID."""
        with TemporaryDirectory(prefix="akuz_phase94_stat_spoof_") as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 5, 96)
            root = home / "spool"
            first, _ = build(root, sources, True)
            previous = signatures(root)
            original_ids = [x["id"] for x in first["reports"]]
            combined_id = first["combined"]["id"]
            file = sources / "20260924_A.log"
            before = file.stat()
            old = file.read_bytes()
            replacement = old.replace(b"synthetic event",
                                      b"synthetix event", 1)
            self.assertEqual(len(replacement), len(old))
            file.write_bytes(replacement)
            os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
            after = file.stat()
            self.assertEqual(
                (after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns),
                (before.st_dev, before.st_ino, before.st_size,
                 before.st_mtime_ns))
            self.assertNotEqual(sha256(file), hashlib.sha256(old).hexdigest())
            with patch.dict(os.environ, {"AKUZ_VERIFY_LOCAL_SOURCE_SHA": "1"}):
                with self.assertRaisesRegex(
                        FetchError, "Содержимое локального журнала отличается"):
                    build(root, sources, True)
            self.assertEqual(signatures(root), previous)
            self.assertEqual(
                [x["id"] for x in first["reports"]], original_ids)
            self.assertIn(combined_id, load_store(root)["reports"])
            self.assertTrue(no_ephemeral_spools(root))
            self.assertEqual(file.read_bytes(), replacement)

    def test_default_local_warm_does_not_hash_original_again(self):
        """Strict source digest is never silently enabled on the fast path."""
        with TemporaryDirectory(prefix="akuz_phase94_default_sha_") as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 5, 96)
            root = home / "spool"
            first, _ = build(root, sources, True)
            before = signatures(root)
            with patch.dict(os.environ, {"AKUZ_VERIFY_LOCAL_SOURCE_SHA": "0"}):
                with patch("akuz_app.verify_local_source_sha",
                           side_effect=AssertionError(
                               "unexpected original-source SHA")) as checked:
                    warm, _ = build(root, sources, True)
                checked.assert_not_called()
            self.assertTrue(warm["reused"])
            self.assertEqual([r["id"] for r in warm["reports"]],
                             [r["id"] for r in first["reports"]])
            self.assertEqual(warm["combined"]["id"], first["combined"]["id"])
            self.assertEqual(before, signatures(root))
            self.assertTrue(no_ephemeral_spools(root))

    def test_strict_local_sha_clean_warm_reuses_without_new_report(self):
        with TemporaryDirectory(prefix="akuz_phase94_strict_warm_") as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 5, 96)
            root = home / "spool"
            first, _ = build(root, sources, True)
            baseline = signatures(root)
            from akuz_local import verify_local_source_sha
            with patch.dict(os.environ, {"AKUZ_VERIFY_LOCAL_SOURCE_SHA": "on"}):
                with patch("akuz_app.verify_local_source_sha",
                           wraps=verify_local_source_sha) as checked:
                    result, _ = build(root, sources, True)
                self.assertEqual(checked.call_count, 3)
            self.assertTrue(result["reused"])
            self.assertEqual([row["id"] for row in result["reports"]],
                             [row["id"] for row in first["reports"]])
            self.assertEqual(result["combined"]["id"],
                             first["combined"]["id"])
            self.assertEqual(signatures(root), baseline)
            self.assertTrue(no_ephemeral_spools(root))

    def test_strict_local_sha_uses_report_proof_after_downloads_clear(self):
        with TemporaryDirectory(prefix="akuz_phase94_strict_no_download_") as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 5, 96)
            root = home / "spool"
            first, _ = build(root, sources, True)
            previous_ids = {x["id"] for x in first["reports"]}
            cfg = load_local_config(str(sources), root)
            cleared = clear_cache(root, cfg, load_store(root), False)
            self.assertEqual(cleared["downloads_removed"], 3)
            self.assertEqual(len(load_store(root)["reports"]), 4)
            source = sources / "20260924_A.log"
            stat = source.stat()
            old = source.read_bytes()
            source.write_bytes(old.replace(b"synthetic event",
                                          b"synthetix event", 1))
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            with patch.dict(os.environ, {"AKUZ_VERIFY_LOCAL_SOURCE_SHA": "true"}):
                with self.assertRaisesRegex(
                        FetchError, "Содержимое локального журнала отличается"):
                    build(root, sources, True)
            self.assertEqual(load_store(root)["downloads"], {})
            self.assertTrue(previous_ids.issubset(set(load_store(root)["reports"])))
            self.assertTrue(no_ephemeral_spools(root))

    def test_strict_local_sha_invalid_mode_fails_closed(self):
        with TemporaryDirectory(prefix="akuz_phase94_strict_env_") as td:
            home = Path(td)
            sources = home / "sources"
            create_sources(sources, 5, 96)
            root = home / "spool"
            with patch.dict(os.environ, {"AKUZ_VERIFY_LOCAL_SOURCE_SHA": "typo"}):
                with self.assertRaisesRegex(
                        FetchError, "Неверное значение AKUZ_VERIFY_LOCAL_SOURCE_SHA"):
                    build(root, sources, True)
            self.assertFalse(list((root / "reports").glob("v4_*")))
            self.assertTrue(no_ephemeral_spools(root))

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
