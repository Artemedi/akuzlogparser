"""Phase 13 benchmark public-surface guards."""
import unittest
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import bench_phase13_catalog_error_index as phase13
from akuz_analytics import connect
from akuz_diagnostics import event


class Phase13BenchmarkSurfaceTests(unittest.TestCase):
    def test_refresh_stage_durations_survive_trace_parsing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)

            def measured_refresh(*args, **kwargs):
                for stage, duration in (
                        ("analytics.export", 0.25),
                        ("analytics.overview", 0.5),
                        ("analytics.inventory", 0.062)):
                    event(root, stage, "done", elapsed_s=duration)
                return {}

            with patch.object(phase13, "refresh", side_effect=measured_refresh), \
                 patch.object(phase13, "_db_metrics", return_value={}), \
                 patch.object(phase13, "sql_fingerprint", return_value={}), \
                 patch.object(phase13, "semantic_sql", return_value={}), \
                 patch.object(phase13, "_export_hashes", return_value={}), \
                 patch.object(phase13, "semantic_exports", return_value={}):
                metrics = phase13._run_refresh(root, True)["metrics"]

            self.assertEqual(metrics["export_elapsed_s"], 0.25)
            self.assertEqual(metrics["overview_elapsed_s"], 0.5)
            self.assertEqual(metrics["inventory_elapsed_s"], 0.062)

    def test_db_metrics_closes_handle_before_next_trial(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with closing(connect(root)):
                pass
            original_connect = sqlite3.connect
            retained = []

            def capture(*args, **kwargs):
                db = original_connect(*args, **kwargs)
                retained.append(db)
                return db

            try:
                with patch.object(phase13.sqlite3, "connect", side_effect=capture):
                    phase13._db_metrics(root)
                with self.assertRaises(sqlite3.ProgrammingError):
                    retained[0].execute("SELECT 1")
                phase13._reset_analytics(root)
                self.assertFalse(
                    (root / "cache" / "error_analytics.sqlite").exists())
            finally:
                for db in retained:
                    db.close()

    def test_public_summary_redacts_private_evidence(self):
        result = dict(
            setup=dict(source_bytes=123, reports=4),
            baseline=dict(wall_s=10.0),
            candidate=dict(wall_s=5.0),
            wall_reduction_pct=50.0,
            raw_read_reduction_pct=90.0,
            recognize_reduction_pct=95.0,
            exact_sql_equivalence=True,
            exact_export_equivalence=True,
            sql_fingerprint_hash="secret-hash",
            export_fingerprint_hash="secret-export",
            host="secret-host",
            remote_path="/secret/log",
        )
        public = phase13._public(result)
        rendered = repr(public)
        self.assertNotIn("secret-host", rendered)
        self.assertNotIn("/secret/log", rendered)
        self.assertNotIn("secret-hash", rendered)
        self.assertNotIn("secret-export", rendered)


if __name__ == "__main__":
    unittest.main()
