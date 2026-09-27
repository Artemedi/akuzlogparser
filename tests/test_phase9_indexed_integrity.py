"""Persistently quarantine only owned indexed reports with failed integrity."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from akuz_app import _publish
from akuz_store import load_store, report_summary
from akuz_analytics import reports as analytics_reports
from tests import test_phase9_report_crash as crash_fixture


class IndexedIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='akuz_phase9_index_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'synthetic.log'
        self.source.write_text('synthetic', encoding='utf-8')

    def publish(self, store=None):
        return _publish(self.root, load_store(self.root) if store is None else store,
                        'synthetic-key', self.source, None, [], 'synthetic',
                        'single', gen_fn=crash_fixture.ReportCrashTests.generator)

    def test_corrupt_catalog_quarantined_without_deleting_old_report(self):
        first = self.publish()
        old = self.root/'reports'/first['id']
        (old/'data'/'catalog.js').write_text('broken', encoding='utf-8')
        second = self.publish()
        self.assertFalse(second['reused'])
        self.assertNotEqual(first['id'], second['id'])
        self.assertTrue(old.exists())
        persisted = load_store(self.root)
        self.assertEqual(persisted['reports'][first['id']]['invalidated'],
                         'integrity')
        self.assertIn('⚠ Повреждён', next(x['label'] for x in
            report_summary(persisted, self.root) if x['id'] == first['id']))
        self.assertEqual([row[0] for row in analytics_reports(self.root)],
                         [second['id']])
        self.assertEqual(self.publish()['id'], second['id'])
        self.assertEqual(len(load_store(self.root)['reports']), 2)

    def test_missing_raw_shard_quarantines_indexed_report(self):
        first = self.publish()
        original = self.root/'reports'/first['id']
        (original/'data'/'raw_0000.js').unlink()
        second = self.publish()
        self.assertFalse(second['reused'])
        self.assertTrue(original.is_dir())
        self.assertEqual(load_store(self.root)['reports'][first['id']]
                         ['invalidated'], 'integrity')

    def test_failed_quarantine_inventory_write_does_not_publish_new(self):
        first = self.publish()
        (self.root/'reports'/first['id']/'data'/'catalog.js').write_text(
            'broken', encoding='utf-8')
        old_bytes = (self.root/'cache'/'inventory.json').read_bytes()
        store = load_store(self.root)
        with patch('akuz_store.save_store',
                   side_effect=OSError('quarantine write failed')):
            with self.assertRaisesRegex(OSError, 'quarantine write failed'):
                self.publish(store)
        self.assertNotIn('invalidated', store['reports'][first['id']])
        self.assertEqual((self.root/'cache'/'inventory.json').read_bytes(),
                         old_bytes)
        self.assertEqual(len(list((self.root/'reports').glob('v4_*'))), 1)


    def test_real_generator_quarantine_preserves_analytics_and_sources(self):
        from akuz_app import State, perform_list, perform_build_current
        from akuz_analytics import source_inventory, reports as indexed
        from scripts.bench_phase9_baseline import create_sources
        sources = self.root/'sources'
        create_sources(sources, 6, 72)
        def run_build():
            state = State()
            perform_list(self.root, state, source='local',
                         local_path=str(sources))
            selected = [dict(id=row['id'], date='') for row in state.listing]
            perform_build_current(self.root, state, selected,
                                  use_derived_spool=True)
            self.assertFalse(state.result['analytics_warning'])
            return state.result
        first = run_build()
        self.assertEqual(len(first['reports']), 3)
        target_id = first['reports'][0]['id']
        (self.root/'reports'/target_id/'data'/'catalog.js').write_text(
            'CORRUPTED', encoding='utf-8')
        second = run_build()
        self.assertFalse(second['reports'][0]['reused'])
        self.assertEqual(len(indexed(self.root)), 4)
        self.assertNotIn(target_id, [row[0] for row in indexed(self.root)])
        self.assertEqual(len(source_inventory(self.root)), 3)
        self.assertTrue(run_build()['reused'])
