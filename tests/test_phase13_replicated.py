"""Phase 13 replicated benchmark surface guards."""
import unittest

from scripts import bench_phase13_catalog_error_index_replicated as repl


class Phase13ReplicatedSurfaceTests(unittest.TestCase):
    def test_order_is_balanced_and_public_surface_has_no_trials(self):
        self.assertEqual(
            repl.ORDER, (False, True, True, False, False, True))
        result = dict(
            setup=dict(source_bytes=123, reports=4),
            order=["baseline","candidate","candidate",
                   "baseline","baseline","candidate"],
            summary={
                "baseline": {"wall_s_median": 10.0},
                "candidate": {"wall_s_median": 5.0},
            },
            wall_reduction_pct=50.0,
            cpu_reduction_pct=40.0,
            raw_read_reduction_pct=1.0,
            recognize_reduction_pct=90.0,
            exact_equivalence_6_of_6=True,
            trials=[{"secret": "do-not-print"}],
        )
        public = repl._public(result)
        self.assertNotIn("trials", public)
        self.assertNotIn("do-not-print", repr(public))


if __name__ == "__main__":
    unittest.main()
