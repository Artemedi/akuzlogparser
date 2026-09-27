"""No original AKUZ inputs are needed to check the real fresh-all harness."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from akuz_store import sha256
from scripts.bench_phase94_combined_only import link_real_sources, REAL_DAYS
from scripts.bench_phase94_real_fresh_all import source_guard, summarize, ORDER


class FreshAllHarnessTests(unittest.TestCase):
    def test_balanced_order(self):
        self.assertEqual(len(ORDER),9)
        for mode in ('control','tee','b_lite'):
            self.assertEqual(ORDER.count(mode),3)

    def test_immutable_links_and_mutation_fail_closed(self):
        with TemporaryDirectory() as td:
            root=Path(td)
            downloads=root/'downloads'
            downloads.mkdir()
            for day in REAL_DAYS:
                (downloads/('akuz_v4_fixture_'+day+'_server.log')).write_bytes(
                    ('fixed '+day).encode('ascii'))
            evidence,originals=link_real_sources(downloads,root/'owned')
            source_guard(root/'owned',originals,evidence)
            saved=evidence[0]['sha256']
            original=originals[REAL_DAYS[0]]
            original.write_bytes(b'mutated' + b' '*10)
            self.assertNotEqual(saved,sha256(original))
            with self.assertRaisesRegex(AssertionError,'snapshot changed'):
                source_guard(root/'owned',originals,evidence)

    def test_statistics_each_mode_kept_distinct(self):
        rows=[]
        for i,mode in enumerate(ORDER):
            rows.append(dict(mode=mode,wall_s=10+i,cpu_s=5+i,
                report_bytes=100,sidecar_bytes=10 if mode=='b_lite' else 0,
                peak_ws_bytes=1000+i,sampled_private_peak_bytes=500+i,
                memory_samples=200,unreadable_memory_samples=0))
        summary=summarize(rows)
        self.assertEqual(summary['control']['wall_s']['median'],14)
        self.assertEqual(summary['b_lite']['sidecar_bytes']['median'],10)
        self.assertEqual(summary['tee']['unreadable_memory_samples'],0)


if __name__=='__main__':
    unittest.main()
