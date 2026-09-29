"""Synthetic guards for the P13-05 replicated transaction harness."""
import unittest
from scripts import bench_phase13_single_transaction_replicated as replicated

class Phase13SingleTransactionReplicatedTests(unittest.TestCase):
    def test_balanced_order_and_medians(self):
        self.assertEqual(replicated.ORDER,("B","C","C","B","B","C"))
        trials=[{"metrics":{k:v for k in replicated._MEDIAN_KEYS}} for v in (3,1,2)]
        med=replicated._median_metrics(trials)
        for k in replicated._MEDIAN_KEYS:
            self.assertEqual(med[k],2)

    def test_public_summary_redacts_private_setup_fields(self):
        result={
            "setup":{"source_bytes":123,"reports":4,"host":"secret-host","remote_path":"/secret/path","digest":"secret"},
            "order":list(replicated.ORDER),
            "trials":[{"ordinal":1,"mode":"B","metrics":{
                "wall_s":10.0,"cpu_s":9.0,"ingest_elapsed_s":8.0,
                "transaction_batch_elapsed_s":0.0,"transaction_commits":4,
                "pending_reports":4,"db_bytes":4096,"page_count":1}}],
            "baseline_median":{"wall_s":10.0,"transaction_commits":4},
            "candidate_median":{"wall_s":9.0,"transaction_commits":1},
            "wall_reduction_pct":10.0,"cpu_reduction_pct":5.0,
            "ingest_reduction_pct":4.0,"commit_reduction_pct":75.0,
            "exact_equivalence":True,"inventory_unchanged":True,
        }
        rendered=repr(replicated._public(result))
        self.assertNotIn("secret-host",rendered)
        self.assertNotIn("/secret/path",rendered)
        self.assertNotIn("secret",rendered)

if __name__=="__main__":
    unittest.main()
