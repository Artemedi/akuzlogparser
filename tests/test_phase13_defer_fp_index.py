"""Phase 13 P13-03 deferred-index benchmark public-surface guards."""
import unittest
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import bench_phase13_defer_fp_index as defer_fp
from scripts import bench_phase13_catalog_error_index as benchmark
from akuz_analytics import connect
from akuz_diagnostics import event


class Phase13DeferredFpIndexSurfaceTests(unittest.TestCase):
    def test_refresh_stage_durations_survive_trace_parsing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            def measured_refresh(*args, **kwargs):
                for stage, duration in (
                        ('analytics.index_build', 0.125),
                        ('analytics.export', 0.25),
                        ('analytics.overview', 0.5),
                        ('analytics.inventory', 0.062)):
                    event(root, stage, 'done', elapsed_s=duration)
                return {}
            with patch.object(benchmark, 'refresh', side_effect=measured_refresh), \
                 patch.object(benchmark, '_db_metrics', return_value={}), \
                 patch.object(benchmark, 'sql_fingerprint', return_value={}), \
                 patch.object(benchmark, 'semantic_sql', return_value={}), \
                 patch.object(benchmark, '_export_hashes', return_value={}), \
                 patch.object(benchmark, 'semantic_exports', return_value={}):
                metrics = benchmark._run_refresh(root, True, True)['metrics']
            self.assertEqual(metrics['index_build_elapsed_s'], 0.125)
            self.assertEqual(metrics['export_elapsed_s'], 0.25)
            self.assertEqual(metrics['overview_elapsed_s'], 0.5)
            self.assertEqual(metrics['inventory_elapsed_s'], 0.062)

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
                with patch.object(benchmark.sqlite3, 'connect', side_effect=capture):
                    benchmark._db_metrics(root)
                with self.assertRaises(sqlite3.ProgrammingError):
                    retained[0].execute('SELECT 1')
                benchmark._reset_analytics(root)
                self.assertFalse((root / 'cache' / 'error_analytics.sqlite').exists())
            finally:
                for db in retained:
                    db.close()

    def test_public_summary_redacts_private_evidence(self):
        result = dict(
            setup=dict(source_bytes=123, reports=4),
            baseline=dict(wall_s=10.0),
            candidate=dict(wall_s=8.0),
            wall_reduction_pct=20.0,
            cpu_reduction_pct=15.0,
            ingest_reduction_pct=25.0,
            exact_equivalence=True,
            index_schema_equivalence=True,
            host="secret-host",
            remote_path="/secret/path",
            digest="secret-digest",
        )
        public = defer_fp._public(result)
        rendered = repr(public)
        self.assertNotIn("secret-host", rendered)
        self.assertNotIn("/secret/path", rendered)
        self.assertNotIn("secret-digest", rendered)


if __name__ == "__main__":
    unittest.main()
