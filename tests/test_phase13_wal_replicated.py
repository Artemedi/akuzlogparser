"""Synthetic guards for the P13-04 replicated WAL benchmark."""
import unittest

from scripts import bench_phase13_wal_replicated as replicated


class Phase13WalReplicatedTests(unittest.TestCase):
    def test_balanced_order_and_medians(self):
        self.assertEqual(
            replicated.ORDER,
            ("D", "W", "W", "D", "D", "W"),
        )
        trials = [
            {"metrics": {key: value for key in replicated._MEDIAN_KEYS}}
            for value in (3, 1, 2)
        ]
        medians = replicated._median_metrics(trials)
        for key in replicated._MEDIAN_KEYS:
            self.assertEqual(medians[key], 2)

    def test_public_summary_redacts_private_setup_fields(self):
        result = {
            "setup": {
                "source_bytes": 123,
                "reports": 4,
                "host": "secret-host",
                "remote_path": "/secret/path",
                "digest": "secret-digest",
            },
            "order": list(replicated.ORDER),
            "trials": [
                {
                    "ordinal": 1,
                    "mode": "D",
                    "journal_mode": "DELETE",
                    "metrics": {
                        "wall_s": 10.0,
                        "cpu_s": 9.0,
                        "ingest_elapsed_s": 8.0,
                        "db_bytes": 4096,
                        "storage_bytes": 4096,
                        "page_count": 1,
                        "wal_bytes": 0,
                        "shm_bytes": 0,
                        "journal_bytes": 0,
                    },
                }
            ],
            "baseline_median": {"wall_s": 10.0, "storage_bytes": 4096},
            "candidate_median": {"wall_s": 9.0, "storage_bytes": 4096},
            "wall_reduction_pct": 10.0,
            "cpu_reduction_pct": 5.0,
            "ingest_reduction_pct": 7.0,
            "storage_change_pct": 0.0,
            "exact_equivalence": True,
            "index_schema_equivalence": True,
            "inventory_unchanged": True,
        }
        rendered = repr(replicated._public(result))
        self.assertNotIn("secret-host", rendered)
        self.assertNotIn("/secret/path", rendered)
        self.assertNotIn("secret-digest", rendered)


if __name__ == "__main__":
    unittest.main()
