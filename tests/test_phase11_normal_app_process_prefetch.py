"""Phase 11 normal-app process-prefetch acceptance gates.

Synthetic SSH only: no network, credentials or real logs. The real normal-app
transaction/report/spool/analytics paths run. ProcessFetch itself is replaced
by a deterministic fake because its real Windows spawn lifecycle is covered
separately in test_phase11_process_lifecycle.py.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import akuz_app
from akuz_app import State
from akuz_fetch import ConnectConfig, selected_snapshot_path
from akuz_process_fetch import ProcessFetchError, ProcessFetchResult
from akuz_store import load_store
from scripts.bench_phase9_baseline import create_sources, inventory_manifest
from scripts.phase9_semantic import semantic_exports, semantic_sql


class FakeProcessFetch:
    active = 0
    max_active = 0
    starts = []

    @classmethod
    def reset(cls):
        cls.active = 0
        cls.max_active = 0
        cls.starts = []

    def __init__(self, ctx, target, args, destination, **kwargs):
        self.cfg, self.remote = args
        self.destination = Path(destination)
        self.started = False
        self.finished = False

    def start(self):
        type(self).active += 1
        type(self).max_active = max(type(self).max_active, type(self).active)
        type(self).starts.append(self.remote["name"])
        self.started = True
        return self

    def finish(self):
        payload = self.remote["_payload"]
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination.write_bytes(payload)
        self.finished = True
        type(self).active -= 1
        digest = hashlib.sha256(payload).hexdigest()
        return ProcessFetchResult(
            path=self.destination, digest=digest, bytes=len(payload),
            child_cpu_s=.1, child_fetch_wall_s=.2, ready_latency_s=.3,
            metadata={
                "active": False, "captured_bytes": len(payload),
                "stored_bytes": len(payload), "dropped_tail_bytes": 0,
                "listed_bytes": len(payload),
                "remote_path": self.remote["path"]})

    def close(self):
        if self.started and not self.finished:
            type(self).active -= 1
            self.destination.unlink(missing_ok=True)
            self.finished = True


class FailingProcessFetch(FakeProcessFetch):
    def finish(self):
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination.write_bytes(b"partial")
        type(self).active -= 1
        self.finished = True
        self.destination.unlink(missing_ok=True)
        raise ProcessFetchError("injected child failure")


class NormalAppProcessPrefetchTests(unittest.TestCase):
    def make_remote(self, home):
        source_dir = home / "source_payloads"
        create_sources(source_dir, 17, 128)
        rows = []
        for index, path in enumerate(sorted(source_dir.glob("*.log"))):
            payload = path.read_bytes()
            remote_path = "/srv/akuz/" + path.name
            fid = hashlib.sha256(
                f"ssh-test\0{remote_path}\0{len(payload)}\0{index}".encode()
            ).hexdigest()
            rows.append(dict(
                id=fid, name=path.name, path=remote_path,
                size=len(payload), mtime=1000.0 + index,
                mtime_raw=str(1000 + index), device=77, inode=900 + index,
                modified_utc=f"2026-09-{24+index:02}T12:00:00+00:00",
                suggested_date=f"2026-09-{24+index:02}",
                _payload=payload))
        return rows

    def config(self, root):
        return ConnectConfig(
            "synthetic-host", 22, "reader", "", "", "", "/srv/akuz",
            root / "downloads", "*.log", "", False)

    def fake_fetch(self, calls):
        def fetch(cfg, remote, notify, trace_root=None):
            calls.append(remote["name"])
            target = selected_snapshot_path(cfg, remote)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise AssertionError("synthetic serial fetch would overwrite")
            payload = remote["_payload"]
            target.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            return target, digest, {
                "active": False, "captured_bytes": len(payload),
                "stored_bytes": len(payload), "dropped_tail_bytes": 0,
                "listed_bytes": len(payload), "remote_path": remote["path"]}
        fetch._akuz_process_prefetch_compatible = True
        return fetch

    def build(self, root, rows, *, process, subset=None,
              process_class=FakeProcessFetch):
        root.mkdir(parents=True, exist_ok=True)
        state = State()
        state.source = "linux"
        state.listing = [dict(row) for row in rows]
        chosen_rows = rows if subset is None else [rows[i] for i in subset]
        selected = [dict(id=row["id"], date="") for row in chosen_rows]
        calls = []
        fetch = self.fake_fetch(calls)
        env = {"AKUZ_PHASE11_PROCESS_PREFETCH": "1" if process else "0"}
        with patch.dict(os.environ, env),              patch("akuz_app.source_config", return_value=self.config(root)),              patch("akuz_app.source_list",
                   side_effect=lambda cfg, source, notify:
                       [dict(row) for row in rows]),              patch("akuz_app.ProcessFetch", process_class):
            akuz_app.perform_build(
                root, state, selected, fetch_fn=fetch,
                refresh_remote=True, use_derived_spool=True)
        return state.result, calls

    def signatures(self, root):
        return (
            inventory_manifest(root),
            semantic_sql(root),
            semantic_exports(root))

    def test_fresh_three_source_process_prefetch_matches_serial_semantics(self):
        with TemporaryDirectory(prefix="akuz_p11_app_fresh_") as td:
            home = Path(td)
            rows = self.make_remote(home)
            control = home / "serial"
            candidate = home / "process"
            self.build(control, rows, process=False)

            FakeProcessFetch.reset()
            result, calls = self.build(candidate, rows, process=True)
            self.assertEqual(calls, [rows[0]["name"]])
            self.assertEqual(FakeProcessFetch.starts,
                             [rows[1]["name"], rows[2]["name"]])
            self.assertEqual(FakeProcessFetch.max_active, 1)
            self.assertEqual(FakeProcessFetch.active, 0)
            self.assertEqual(self.signatures(candidate), self.signatures(control))
            self.assertFalse(result["reused"])
            self.assertEqual(len(result["reports"]), 3)
            self.assertIsNotNone(result["combined"])
            self.assertFalse(list((candidate / "downloads").glob(
                ".akuz-phase11-prefetch-*")))

    def test_warm_first_source_is_not_refetched_and_fresh_tail_overlaps(self):
        with TemporaryDirectory(prefix="akuz_p11_app_mixed_") as td:
            home = Path(td)
            rows = self.make_remote(home)
            root = home / "app"
            self.build(root, rows, process=False, subset=[0])

            FakeProcessFetch.reset()
            result, calls = self.build(root, rows, process=True)
            self.assertEqual(calls, [rows[1]["name"]])
            self.assertEqual(FakeProcessFetch.starts, [rows[2]["name"]])
            self.assertEqual(FakeProcessFetch.max_active, 1)
            self.assertTrue(result["reports"][0]["reused"])
            self.assertFalse(result["reports"][1]["reused"])
            self.assertFalse(result["reports"][2]["reused"])
            self.assertIsNotNone(result["combined"])
            self.assertEqual(len(load_store(root)["downloads"]), 3)

    def test_orphan_cleanup_is_scoped_to_one_app_root(self):
        with TemporaryDirectory(prefix="akuz_p11_orphan_") as td:
            home = Path(td)
            downloads = home / "downloads"
            downloads.mkdir()
            root_a = home / "app-a"
            root_b = home / "app-b"
            own = downloads / (
                akuz_app._phase11_prefetch_prefix(root_a) + "stale")
            other = downloads / (
                akuz_app._phase11_prefetch_prefix(root_b) + "active")
            normal = downloads / "ordinary-file.log"
            own.mkdir()
            (own / "partial.log").write_bytes(b"partial")
            other.mkdir()
            (other / "keep.log").write_bytes(b"keep")
            normal.write_bytes(b"normal")

            removed = akuz_app._cleanup_phase11_prefetch_orphans(
                root_a, downloads)

            self.assertEqual(removed, 1)
            self.assertFalse(own.exists())
            self.assertTrue(other.is_dir())
            self.assertEqual((other / "keep.log").read_bytes(), b"keep")
            self.assertEqual(normal.read_bytes(), b"normal")

    def test_prefetch_failure_keeps_completed_single_then_restart_recovers(self):
        with TemporaryDirectory(prefix="akuz_p11_app_fault_") as td:
            home = Path(td)
            rows = self.make_remote(home)
            root = home / "app"

            FailingProcessFetch.reset()
            with self.assertRaisesRegex(ProcessFetchError, "injected child failure"):
                self.build(root, rows, process=True,
                           process_class=FailingProcessFetch)
            store = load_store(root)
            self.assertEqual(len(store["reports"]), 1)
            self.assertEqual(len(store["downloads"]), 1)
            self.assertTrue(all(
                report["kind"] == "single"
                for report in store["reports"].values()))
            self.assertFalse(list((root / "reports").glob("*.building")))
            self.assertFalse(list((root / "downloads").glob(
                ".akuz-phase11-prefetch-*")))

            FakeProcessFetch.reset()
            recovered, calls = self.build(root, rows, process=True)
            self.assertTrue(recovered["reports"][0]["reused"])
            self.assertEqual(calls, [rows[1]["name"]])
            self.assertEqual(FakeProcessFetch.starts, [rows[2]["name"]])
            self.assertIsNotNone(recovered["combined"])
            warm, calls = self.build(root, rows, process=True)
            self.assertTrue(warm["reused"])
            self.assertEqual(calls, [])
            self.assertEqual(FakeProcessFetch.starts, [rows[2]["name"]])


if __name__ == "__main__":
    unittest.main()
