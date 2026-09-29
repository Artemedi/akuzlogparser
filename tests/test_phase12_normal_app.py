"""Phase 12 normal-app opt-in policy; synthetic SSH bytes only.

Transport cryptographic behavior is covered separately. These tests verify
inventory proof seeding, candidate selection, fallback, and default-off policy
through the real transactional application build.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import akuz_app as app
from scripts import bench_phase12_normal_app_real as phase12_real
from akuz_fetch import ConnectConfig, DeltaResumeFallback
from akuz_store import load_store, sha256


def event(hour: int, label: str) -> bytes:
    return (
        f"{hour:02d}:00:00.000,AKUZ,req,user: "
        f"System.InvalidOperationException: {label}\n"
        " at AKUZ.Handle()\n"
    ).encode("utf-8")


class ResumeAwareSSH:
    def __init__(self, root: Path):
        self.root = root
        self.device = 77
        self.inode = 501
        self.mtime = 1000
        self.remote_path = "/srv/akuz/20260925_server.log"
        self.backing = root.parent / "remote_20260925_server.log"
        self.backing.write_bytes(event(12, "first"))
        self.cfg = ConnectConfig(
            "ssh.example", 22, "reader", "", "", "",
            "/srv/akuz", root / "downloads", "*.log", "", False)
        self.calls = []
        self.resume_owners = []
        self.fail_resume_once = False

        def fetch(cfg, remote, notify, **kwargs):
            resume = kwargs.get("resume")
            self.calls.append(resume is not None)
            if resume is not None:
                self.resume_owners.append(resume.get("_delta_owner"))
            if resume is not None and self.fail_resume_once:
                self.fail_resume_once = False
                raise DeltaResumeFallback("synthetic proof rejection")
            data = self.backing.read_bytes()
            cfg.local_dest.mkdir(parents=True, exist_ok=True)
            target = cfg.local_dest / (
                "akuz_v4_fake_" + remote["id"][:18] + "_" + remote["name"])
            if target.exists():
                raise AssertionError("synthetic fetch would overwrite snapshot")
            target.write_bytes(data)
            details = dict(
                active=False,
                captured_bytes=len(data),
                stored_bytes=len(data),
                dropped_tail_bytes=0,
                listed_bytes=remote["size"],
                remote_path=remote["path"],
            )
            if resume is not None:
                details["delta_resume"] = True
                details["previous_bytes"] = resume["size"]
            return target, sha256(target), details

        fetch._akuz_delta_resume_compatible = True
        self.fetch = fetch

    def listing(self):
        data = self.backing.read_bytes()
        token = "\0".join((
            self.cfg.host, str(self.cfg.port), self.cfg.username,
            self.remote_path, str(len(data)), str(self.mtime),
            str(self.device), str(self.inode)))
        fid = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return [dict(
            id=fid,
            name="20260925_server.log",
            path=self.remote_path,
            size=len(data),
            mtime=float(self.mtime),
            mtime_raw=str(self.mtime),
            device=self.device,
            inode=self.inode,
            modified_utc="2026-09-25T00:00:00+00:00",
            suggested_date="2026-09-25",
            date="2026-09-25",
        )]

    def append(self, payload: bytes):
        with self.backing.open("ab") as stream:
            stream.write(payload)
        self.mtime += 1


class Phase12NormalAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="akuz_phase12_app_")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / "app"
        self.root.mkdir()
        self.remote = ResumeAwareSSH(self.root)

    def prepare(self):
        state = app.State()
        with patch.object(app, "source_config", return_value=self.remote.cfg):
            app.perform_list(
                self.root, state,
                list_fn=lambda cfg, notify: self.remote.listing(),
                source="linux")
        selected = [dict(id=state.listing[0]["id"], date="")]
        return state, selected

    def build(self, state, selected, delta: str | None):
        env = {} if delta is None else {"AKUZ_PHASE12_DELTA_RESUME": delta}
        with patch.dict(app.os.environ, env, clear=False), \
             patch.object(app, "source_config", return_value=self.remote.cfg), \
             patch.object(
                 app, "source_list",
                 side_effect=lambda cfg, source, notify: self.remote.listing()):
            if delta is None:
                app.os.environ.pop("AKUZ_PHASE12_DELTA_RESUME", None)
            return app.perform_build(
                self.root, state, selected,
                fetch_fn=self.remote.fetch,
                refresh_remote=True,
                use_derived_spool=False)

    def test_default_off_does_not_seed_or_use_delta_metadata(self):
        state, selected = self.prepare()
        self.build(state, selected, None)
        first = next(iter(load_store(self.root)["downloads"].values()))
        self.assertNotIn("delta_proof_version", first["snapshot"])
        self.remote.append(event(13, "second"))
        self.build(state, selected, "1")
        self.assertEqual(self.remote.calls, [False, False])
        self.assertEqual(state.result["delta_resume_downloads"], 0)
        latest = max(
            load_store(self.root)["downloads"].values(),
            key=lambda row: row["size"])
        self.assertEqual(latest["snapshot"]["delta_proof_version"], 1)
        self.assertEqual(
            (latest["snapshot"]["device"], latest["snapshot"]["inode"]),
            (self.remote.device, self.remote.inode))

    def test_opt_in_full_then_growth_uses_proven_previous_snapshot(self):
        state, selected = self.prepare()
        with patch.object(
                app, "_phase11_process_allowed",
                side_effect=AssertionError(
                    "Phase 11 must not be considered while delta opt-in is on")):
            self.build(state, selected, "1")
        first_store = load_store(self.root)
        first_entry = next(iter(first_store["downloads"].values()))
        first_path = Path(first_entry["path"])
        first_bytes = first_path.read_bytes()
        self.assertEqual(first_entry["snapshot"]["delta_proof_version"], 1)

        self.remote.append(event(13, "second"))
        self.build(state, selected, "1")
        self.assertEqual(self.remote.calls, [False, True])
        self.assertEqual(
            self.remote.resume_owners, [app._phase12_delta_owner(self.root)])
        self.assertEqual(state.result["delta_resume_downloads"], 1)
        self.assertEqual(state.result["delta_resume_fallbacks"], 0)
        self.assertEqual(first_path.read_bytes(), first_bytes)
        self.assertEqual(len(load_store(self.root)["downloads"]), 2)

    def test_delta_rejection_reopens_safe_full_fetch(self):
        state, selected = self.prepare()
        self.build(state, selected, "true")
        self.remote.append(event(13, "second"))
        self.remote.fail_resume_once = True
        self.build(state, selected, "true")
        self.assertEqual(self.remote.calls, [False, True, False])
        self.assertEqual(state.result["delta_resume_downloads"], 0)
        self.assertEqual(state.result["delta_resume_fallbacks"], 1)
        newest = max(
            load_store(self.root)["downloads"].values(),
            key=lambda row: row["size"])
        self.assertEqual(
            Path(newest["path"]).read_bytes(), self.remote.backing.read_bytes())

    def test_candidate_rejects_wrong_inode_and_external_path(self):
        state, selected = self.prepare()
        self.build(state, selected, "1")
        store = load_store(self.root)
        entry = next(iter(store["downloads"].values()))
        current = self.remote.listing()[0]
        self.remote.append(event(13, "second"))
        current = self.remote.listing()[0]

        entry["snapshot"]["inode"] += 1
        self.assertIsNone(
            app._phase12_resume_candidate(store, self.remote.cfg, current))

        entry["snapshot"]["inode"] = self.remote.inode
        outside = self.home / "outside.log"
        outside.write_bytes(Path(entry["path"]).read_bytes())
        entry["path"] = str(outside)
        self.assertIsNone(
            app._phase12_resume_candidate(store, self.remote.cfg, current))

    def test_opt_in_cleans_only_this_app_roots_delta_orphans(self):
        state, selected = self.prepare()
        dest = self.remote.cfg.local_dest
        dest.mkdir(parents=True, exist_ok=True)
        owner = app._phase12_delta_owner(self.root)
        owned_part = dest / (".akuz-phase12-" + owner + "-part-dead")
        owned_delta = dest / (".akuz-phase12-" + owner + "-delta-dead")
        foreign = dest / ".akuz-phase12-ffffffffffff-part-keep"
        arbitrary = dest / "operator-file.txt"
        for path in (owned_part, owned_delta, foreign, arbitrary):
            path.write_bytes(b"x")
        self.build(state, selected, "1")
        self.assertFalse(owned_part.exists())
        self.assertFalse(owned_delta.exists())
        self.assertTrue(foreign.exists())
        self.assertTrue(arbitrary.exists())

    def test_portable_build_explicitly_includes_delta_module(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "build_portable.py").read_text("utf-8")
        self.assertIn("'--hidden-import', 'akuz_delta'", script)

    def test_real_gate_public_summary_is_sanitized(self):
        result = dict(
            fixed_prefix_bytes=100,
            previous_bytes=80,
            delta_bytes=20,
            control_wall_s=10.0,
            control_cpu_s=3.0,
            delta_wall_s=5.0,
            delta_cpu_s=2.0,
            wall_reduction_pct=50.0,
            snapshot_equivalence=True,
            report_equivalence=True,
            semantic_sql_equivalence=True,
            semantic_export_equivalence=True,
            delta_resume_downloads=1,
            delta_resume_fallbacks=0,
            host="secret-host",
            remote_path="/secret/path",
            snapshot_sha256="secret-digest",
        )
        public = phase12_real._public(result)
        rendered = repr(public)
        self.assertNotIn("secret-host", rendered)
        self.assertNotIn("/secret/path", rendered)
        self.assertNotIn("secret-digest", rendered)

    def test_invalid_delta_switch_fails_before_fetch(self):
        state, selected = self.prepare()
        with patch.dict(
                app.os.environ,
                {"AKUZ_PHASE12_DELTA_RESUME": "maybe"},
                clear=False), \
             self.assertRaisesRegex(
                 app.FetchError, "AKUZ_PHASE12_DELTA_RESUME"):
            app.perform_build(
                self.root, state, selected,
                fetch_fn=self.remote.fetch,
                refresh_remote=False,
                use_derived_spool=False)
        self.assertEqual(self.remote.calls, [])


if __name__ == "__main__":
    unittest.main()
