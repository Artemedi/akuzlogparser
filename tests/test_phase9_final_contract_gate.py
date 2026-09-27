"""Phase 9.1 complete synthetic fresh/cache/failure contract gates.

Only a TemporaryDirectory is mutated. No SSH, user cache or real .log.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from akuz_store import load_store
from scripts.bench_phase9_baseline import create_sources, inventory_manifest, remove_report
from scripts.phase9_semantic import semantic_sql, semantic_exports
from tests.test_phase9_spool import build


class FinalDerivedContractGate(unittest.TestCase):
    def signatures(self, root):
        return (inventory_manifest(root), semantic_sql(root), semantic_exports(root))

    def test_all_four_cache_transitions_and_cross_mode_parity(self):
        scenarios = ('warm', 'combined_only_miss', 'single_only_miss',
                     'mixed_single_and_combined_miss')
        with TemporaryDirectory(prefix='akuz_phase9_final_contract_') as td:
            home = Path(td)
            sources = home/'sources'
            create_sources(sources, 11, 384)
            for scenario in scenarios:
                with self.subTest(scenario=scenario):
                    control = home/(scenario+'_no_spool')
                    candidate = home/(scenario+'_with_spool')
                    first_control,_ = build(control, sources, False)
                    first_candidate,_ = build(candidate, sources, True)
                    self.assertFalse(first_control['reused'])
                    self.assertFalse(first_candidate['reused'])
                    expected = self.signatures(control)
                    self.assertEqual(expected, self.signatures(candidate))
                    if scenario in ('combined_only_miss', 'mixed_single_and_combined_miss'):
                        remove_report(control, 'combined')
                        remove_report(candidate, 'combined')
                    if scenario in ('single_only_miss', 'mixed_single_and_combined_miss'):
                        remove_report(control, 'single')
                        remove_report(candidate, 'single')
                    old,new = build(control, sources, False)[0], build(candidate, sources, True)[0]
                    self.assertEqual(old['reused'], scenario == 'warm')
                    self.assertEqual(new['reused'], scenario == 'warm')
                    self.assertEqual(expected, self.signatures(control))
                    self.assertEqual(expected, self.signatures(candidate))
                    self.assertEqual([r['reused'] for r in old['reports']],
                                     [r['reused'] for r in new['reports']])
                    self.assertEqual(bool(old['combined'] and old['combined']['reused']),
                                     bool(new['combined'] and new['combined']['reused']))
                    self.assertFalse(list((candidate/'cache').glob('akuz-phase9-derived-*')))

    def test_derived_replay_failure_keeps_completed_singles_and_rebuilds_combined(self):
        with TemporaryDirectory(prefix='akuz_phase9_final_fault_') as td:
            home = Path(td)
            sources = home/'sources'
            create_sources(sources, 10, 256)
            build(home/'reference', sources, False)
            expected = self.signatures(home/'reference')
            root = home/'failed_then_recovered'
            with patch('akuz_app.verified_next',
                       side_effect=ValueError('injected derived replay failure')):
                with self.assertRaisesRegex(ValueError, 'derived replay failure'):
                    build(root, sources, True)
            store = load_store(root)
            self.assertEqual(len(store['reports']), 3)
            self.assertTrue(all(x['kind']=='single' for x in store['reports'].values()))
            self.assertFalse(list((root/'reports').glob('*.building')))
            self.assertFalse(list((root/'cache').glob('akuz-phase9-derived-*')))
            result,_ = build(root, sources, True)
            self.assertTrue(all(r['reused'] for r in result['reports']))
            self.assertFalse(result['combined']['reused'])
            self.assertEqual(self.signatures(root), expected)
            again,_ = build(root, sources, True)
            self.assertTrue(again['reused'])
            self.assertEqual(self.signatures(root), expected)


if __name__ == '__main__':
    unittest.main()
