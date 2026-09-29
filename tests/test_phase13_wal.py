"""Phase 13 P13-04 WAL benchmark public-surface guards."""
import unittest

from scripts import bench_phase13_wal as wal


class Phase13WalSurfaceTests(unittest.TestCase):
    def test_public_summary_redacts_private_evidence(self):
        result = dict(
            setup=dict(source_bytes=123, reports=4),
            baseline=dict(wall_s=10.0),
            candidate=dict(wall_s=9.0),
            wall_reduction_pct=10.0,
            cpu_reduction_pct=5.0,
            storage_change_pct=1.0,
            exact_equivalence=True,
            index_schema_equivalence=True,
            host="secret-host",
            remote_path="/secret/path",
            digest="secret-digest",
        )
        public = wal._public(result)
        rendered = repr(public)
        self.assertNotIn("secret-host", rendered)
        self.assertNotIn("/secret/path", rendered)
        self.assertNotIn("secret-digest", rendered)


if __name__ == "__main__":
    unittest.main()
