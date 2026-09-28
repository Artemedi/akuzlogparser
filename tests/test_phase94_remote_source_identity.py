"""Phase 9.4 normal-app SSH identity gates with synthetic remote bytes only.

The adapter boundary is faked deliberately: no network, credentials, ConnectConf,
or real AKUZ payloads. The app still runs its normal transactional build,
report generation, ephemeral derived spool, inventory and analytics paths.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import akuz_app as app
from akuz_fetch import ConnectConfig, FetchError
from akuz_store import key_for, load_store, sha256
from scripts.phase9_semantic import semantic_exports, semantic_sql


class SyntheticSSH:
    def __init__(self, root: Path, host: str = "ssh-a.example"):
        self.root = root
        self.host = host
        self.device = 77
        self.rows = {}
        self.cfg = ConnectConfig(
            host, 2222, "reader", "", "", "", "/srv/akuz",
            root / "downloads", "*.log", "", False)

    def add(self, name: str, data: bytes, *, inode: int, mtime: int):
        backing = self.root.parent / ("backing_" + name)
        backing.write_bytes(data)
        self.rows["/srv/akuz/" + name] = dict(
            path=backing, inode=inode, mtime=mtime)

    def listing(self):
        result = []
        for remote_path, row in sorted(self.rows.items()):
            backing = row["path"]
            size = backing.stat().st_size
            mtime_raw = str(row["mtime"])
            token = "\0".join((
                self.cfg.host, str(self.cfg.port), self.cfg.username,
                remote_path, str(size), mtime_raw,
                str(self.device), str(row["inode"])))
            fid = hashlib.sha256(token.encode("utf-8")).hexdigest()
            result.append(dict(
                id=fid, name=remote_path.rsplit("/", 1)[-1],
                path=remote_path, size=size, mtime=float(row["mtime"]),
                mtime_raw=mtime_raw, device=self.device, inode=row["inode"],
                modified_utc="2026-09-24T00:00:00+00:00",
                suggested_date="2026-09-24"))
        result.sort(key=lambda x: (x["mtime"], x["name"]), reverse=True)
        return result

    def fetch(self, cfg, remote, notify):
        row = self.rows[remote["path"]]
        data = row["path"].read_bytes()
        cfg.local_dest.mkdir(parents=True, exist_ok=True)
        target = cfg.local_dest / (
            "akuz_v4_fake_" + remote["id"][:18] + "_" + remote["name"])
        if target.exists():
            raise AssertionError("synthetic fetch would overwrite snapshot")
        target.write_bytes(data)
        return target, sha256(target), dict(
            active=False, captured_bytes=len(data), stored_bytes=len(data),
            dropped_tail_bytes=0, listed_bytes=remote["size"],
            remote_path=remote["path"])

    def append(self, name: str, data: bytes):
        remote_path = "/srv/akuz/" + name
        row = self.rows[remote_path]
        with row["path"].open("ab") as stream:
            stream.write(data)
        row["mtime"] += 1

    def rotate(self, name: str, data: bytes):
        remote_path = "/srv/akuz/" + name
        row = self.rows[remote_path]
        row["path"].write_bytes(data)
        row["inode"] += 1000
        row["mtime"] += 1


def source_bytes(label: str):
    return (
        f"12:00:00.000,AKUZ,req,user: System.InvalidOperationException: {label}\n"
        " at AKUZ.Handle()\n"
    ).encode("utf-8")


class RemoteNormalAppIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="akuz_phase94_ssh_identity_")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / "app"
        self.root.mkdir()
        self.remote = SyntheticSSH(self.root)
        self.remote.add("20260924_A.log", source_bytes("same"), inode=101, mtime=1001)
        self.remote.add("20260925_B.log", source_bytes("other"), inode=102, mtime=1002)

    def prepare(self, remote=None):
        remote = remote or self.remote
        state = app.State()
        with patch.object(app, "source_config", return_value=remote.cfg):
            app.perform_list(
                self.root, state,
                list_fn=lambda cfg, notify: remote.listing(),
                source="linux")
        selected = [
            dict(id=row["id"], date="")
            for row in sorted(state.listing, key=lambda x: x["name"])
        ]
        return state, selected

    def build(self, state, selected, remote=None):
        remote = remote or self.remote
        with patch.object(app, "source_config", return_value=remote.cfg), \
             patch.object(
                 app, "source_list",
                 side_effect=lambda cfg, source, notify: remote.listing()):
            return app.perform_build(
                self.root, state, selected, fetch_fn=remote.fetch,
                refresh_remote=True, use_derived_spool=True)

    def test_same_bytes_on_two_remote_paths_remain_independent(self):
        self.remote.rows["/srv/akuz/20260925_B.log"]["path"].write_bytes(
            source_bytes("same"))
        state, selected = self.prepare()
        self.build(state, selected)
        singles = state.result["reports"]
        self.assertEqual(len(singles), 2)
        self.assertEqual(len({x["id"] for x in singles}), 2)
        self.assertEqual(len({x["sources"][0]["remote_path"] for x in singles}), 2)
        self.assertIsNotNone(state.result["combined"])
        self.assertEqual(len(load_store(self.root)["downloads"]), 2)
        # Warm app path must keep both identities, not collapse by content SHA.
        self.build(state, selected)
        self.assertTrue(state.result["reused"])
        self.assertEqual(len(load_store(self.root)["reports"]), 3)

    def test_old_browser_selection_reconciles_remote_growth(self):
        state, selected = self.prepare()
        self.build(state, selected)
        first_ids = {x["sources"][0]["name"]: x["id"]
                     for x in state.result["reports"]}
        combined = state.result["combined"]["id"]
        self.remote.append(
            "20260924_A.log",
            b"13:00:00.000,AKUZ,req,user: appended event\n")
        # Reuse exactly the OLD browser selection; refresh_remote must replace its id.
        self.build(state, selected)
        current = {x["sources"][0]["name"]: x
                   for x in state.result["reports"]}
        self.assertNotEqual(current["20260924_A.log"]["id"],
                            first_ids["20260924_A.log"])
        self.assertFalse(current["20260924_A.log"]["reused"])
        self.assertEqual(current["20260925_B.log"]["id"],
                         first_ids["20260925_B.log"])
        self.assertTrue(current["20260925_B.log"]["reused"])
        self.assertFalse(state.result["combined"]["reused"])
        self.assertNotEqual(state.result["combined"]["id"], combined)
        self.assertEqual(len(load_store(self.root)["reports"]), 5)
        self.assertEqual(len(load_store(self.root)["downloads"]), 3)

    def test_old_browser_selection_rejects_remote_rotation_before_fetch(self):
        state, selected = self.prepare()
        self.build(state, selected)
        reports_before = set(load_store(self.root)["reports"])
        downloads_before = set(load_store(self.root)["downloads"])
        sql_before = semantic_sql(self.root)
        exports_before = semantic_exports(self.root)
        self.remote.rotate(
            "20260924_A.log",
            b"12:00:00.000,AKUZ,req,user: replacement inode\n")
        with self.assertRaisesRegex(FetchError, "ротирован"):
            self.build(state, selected)
        self.assertEqual(set(load_store(self.root)["reports"]), reports_before)
        self.assertEqual(set(load_store(self.root)["downloads"]), downloads_before)
        self.assertEqual(semantic_sql(self.root), sql_before)
        self.assertEqual(semantic_exports(self.root), exports_before)
        self.assertFalse(list((self.root / "reports").glob("*.building")))
        self.assertFalse(list((self.root / "cache").glob("akuz-phase9-derived-*")))

    def test_same_remote_path_on_different_host_is_new_source_identity(self):
        state, selected = self.prepare()
        self.build(state, selected)
        first_report_ids = {x["id"] for x in state.result["reports"]}
        first_combined = state.result["combined"]["id"]

        other = SyntheticSSH(self.root, host="ssh-b.example")
        for remote_path, row in self.remote.rows.items():
            # Same remote pathname and same bytes/metadata, different trusted host.
            other.rows[remote_path] = dict(
                path=row["path"], inode=row["inode"], mtime=row["mtime"])
        state2, selected2 = self.prepare(other)
        self.build(state2, selected2, other)
        self.assertFalse(state2.result["reused"])
        self.assertTrue(first_report_ids.isdisjoint(
            {x["id"] for x in state2.result["reports"]}))
        self.assertNotEqual(first_combined, state2.result["combined"]["id"])
        self.assertEqual(len(load_store(self.root)["reports"]), 6)
        self.assertEqual(len(load_store(self.root)["downloads"]), 4)


if __name__ == "__main__":
    unittest.main()
