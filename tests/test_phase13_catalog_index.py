"""Phase 13 benchmark public-surface guards."""
import unittest

from scripts import bench_phase13_catalog_error_index as phase13


class Phase13BenchmarkSurfaceTests(unittest.TestCase):
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
