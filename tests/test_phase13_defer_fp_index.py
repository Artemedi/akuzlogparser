"""Phase 13 P13-03 deferred-index benchmark public-surface guards."""
import unittest

from scripts import bench_phase13_defer_fp_index as defer_fp


class Phase13DeferredFpIndexSurfaceTests(unittest.TestCase):
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
