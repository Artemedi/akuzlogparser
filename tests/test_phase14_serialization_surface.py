"""Phase 14 real baseline public-surface guard."""
import unittest

from scripts import bench_phase14_serialization as phase14


class Phase14SerializationSurfaceTests(unittest.TestCase):
    def test_public_summary_redacts_private_evidence(self):
        result = dict(
            setup=dict(source_bytes=123, reports=4),
            totals=dict(shard_bytes=10, catalog_bytes=5),
            memory=dict(peak_working_set_bytes=100),
            host="secret-host",
            remote_path="/secret/path",
            digest="secret-digest",
        )
        public = phase14._public(result)
        rendered = repr(public)
        self.assertNotIn("secret-host", rendered)
        self.assertNotIn("/secret/path", rendered)
        self.assertNotIn("secret-digest", rendered)


if __name__ == "__main__":
    unittest.main()
