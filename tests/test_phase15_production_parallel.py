"""Production gates for Phase 15 opt-in parallel single-report generation."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import akuz_app
from akuz_app import State, perform_build, perform_list
from akuz_store import load_store
from scripts.bench_phase9_baseline import file_manifest


class Phase15PolicyTests(unittest.TestCase):
    def test_default_off_and_explicit_values(self):
        self.assertFalse(akuz_app._phase15_parallel_requested({}))
        for value in ("1", "true", "YES", "on"):
            self.assertTrue(akuz_app._phase15_parallel_requested(
                {"AKUZ_PHASE15_PARALLEL_GENERATION": value}))
        for value in ("0", "false", "NO", "off"):
            self.assertFalse(akuz_app._phase15_parallel_requested(
                {"AKUZ_PHASE15_PARALLEL_GENERATION": value}))
        with self.assertRaisesRegex(
                Exception, "AKUZ_PHASE15_PARALLEL_GENERATION"):
            akuz_app._phase15_parallel_requested(
                {"AKUZ_PHASE15_PARALLEL_GENERATION": ""})

    def test_scope_requires_windows_builtin_two_sources_and_no_delta(self):
        self.assertTrue(akuz_app._phase15_parallel_allowed(
            True, [{}, {}], akuz_app.generate,
            delta_requested=False, client_os="nt"))
        self.assertFalse(akuz_app._phase15_parallel_allowed(
            False, [{}, {}], akuz_app.generate,
            delta_requested=False, client_os="nt"))
        self.assertFalse(akuz_app._phase15_parallel_allowed(
            True, [{}], akuz_app.generate,
            delta_requested=False, client_os="nt"))
        self.assertFalse(akuz_app._phase15_parallel_allowed(
            True, [{}, {}], lambda *a, **k: None,
            delta_requested=False, client_os="nt"))
        self.assertFalse(akuz_app._phase15_parallel_allowed(
            True, [{}, {}], akuz_app.generate,
            delta_requested=True, client_os="nt"))
        self.assertFalse(akuz_app._phase15_parallel_allowed(
            True, [{}, {}], akuz_app.generate,
            delta_requested=False, client_os="posix"))


@unittest.skipUnless(os.name == "nt", "Windows Job-bound production gate")
class Phase15NormalAppParityTests(unittest.TestCase):
    def _write_sources(self, root: Path):
        source = root / "source"
        source.mkdir()
        for index, day in enumerate(("20260923", "20260924", "20260925")):
            rows = []
            for n in range(70 + index * 15):
                rows.append(
                    f"12:{n % 60:02}:00.000,AKUZ,req,user: "
                    f"synthetic phase15 {index}-{n}\n")
            (source / f"{day}_server.log").write_text(
                "".join(rows), encoding="utf-8")
        return source

    def _build(self, app_root: Path, source: Path, enabled: bool):
        state = State()
        perform_list(
            app_root, state, source="local", local_path=str(source))
        with state.lock:
            selected = [
                {"id": row["id"], "date": row["date"]}
                for row in reversed(state.listing)
            ]
        with patch.dict(
                os.environ,
                {
                    "AKUZ_PHASE15_PARALLEL_GENERATION": "1" if enabled else "0",
                    "AKUZ_PHASE11_PROCESS_PREFETCH": "0",
                    "AKUZ_PHASE12_DELTA_RESUME": "0",
                },
                clear=False):
            perform_build(app_root, state, selected)
        return state, load_store(app_root)

    @staticmethod
    def _producer_reports(root: Path, store):
        out = {}
        for entry in store["reports"].values():
            folder = root / "reports" / entry["id"]
            manifest = file_manifest(folder)
            manifest.pop("provenance.json", None)
            out[(entry["kind"], entry["label"])] = (
                entry["events"], entry["lines"], manifest)
        return out

    def test_normal_app_parallel_matches_serial_including_combined(self):
        with tempfile.TemporaryDirectory(
                prefix="akuz-p15-prod-test-") as temp:
            root = Path(temp)
            source = self._write_sources(root)
            serial_root = root / "serial-app"
            parallel_root = root / "parallel-app"
            serial_root.mkdir()
            parallel_root.mkdir()

            serial_state, serial_store = self._build(
                serial_root, source, False)
            parallel_state, parallel_store = self._build(
                parallel_root, source, True)

            self.assertEqual(
                self._producer_reports(serial_root, serial_store),
                self._producer_reports(parallel_root, parallel_store))
            self.assertEqual(
                len(serial_store["reports"]),
                len(parallel_store["reports"]))
            self.assertEqual(
                serial_state.result["combined"]["events"],
                parallel_state.result["combined"]["events"])
            self.assertEqual(
                serial_state.result["reports"][0]["events"]
                + serial_state.result["reports"][1]["events"]
                + serial_state.result["reports"][2]["events"],
                parallel_state.result["combined"]["events"])


if __name__ == "__main__":
    unittest.main()
