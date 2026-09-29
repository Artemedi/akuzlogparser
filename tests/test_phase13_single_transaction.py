"""Phase 13 P13-05 transaction benchmark public-surface guards."""
import unittest

from scripts import bench_phase13_single_transaction as single_tx


class Phase13SingleTransactionSurfaceTests(unittest.TestCase):
    def test_public_summary_redacts_private_evidence(self):
        result = dict(
            setup=dict(source_bytes=123, reports=4),
            baseline=dict(wall_s=10.0),
            candidate=dict(wall_s=9.0),
            wall_reduction_pct=10.0,
            cpu_reduction_pct=5.0,
            commit_reduction_pct=75.0,
            exact_equivalence=True,
            host="secret-host",
            remote_path="/secret/path",
            digest="secret-digest",
        )
        public = single_tx._public(result)
        rendered = repr(public)
        self.assertNotIn("secret-host", rendered)
        self.assertNotIn("/secret/path", rendered)
        self.assertNotIn("secret-digest", rendered)


if __name__ == "__main__":
    unittest.main()
