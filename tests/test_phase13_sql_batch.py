"""Phase 13 P13-02 SQL-batch benchmark public-surface guards."""
import unittest

from scripts import bench_phase13_sql_batch as sql_batch


class Phase13SqlBatchSurfaceTests(unittest.TestCase):
    def test_public_summary_redacts_private_evidence(self):
        result = dict(
            setup=dict(source_bytes=123, reports=4),
            baseline=dict(wall_s=10.0),
            candidate=dict(wall_s=8.0),
            wall_reduction_pct=20.0,
            cpu_reduction_pct=15.0,
            point_select_reduction_pct=100.0,
            exact_equivalence=True,
            host="secret-host",
            remote_path="/secret/path",
            digest="secret-digest",
        )
        public = sql_batch._public(result)
        rendered = repr(public)
        self.assertNotIn("secret-host", rendered)
        self.assertNotIn("/secret/path", rendered)
        self.assertNotIn("secret-digest", rendered)


if __name__ == "__main__":
    unittest.main()
