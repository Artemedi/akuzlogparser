"""Document observable report publication state after synthetic process exit."""
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import akuz_app
from akuz_store import load_store, save_store

ROOT = Path(__file__).resolve().parents[1]
CRASH_CHILD = """
import os
import sys
from pathlib import Path
import akuz_app
from akuz_store import load_store
root, phase = Path(sys.argv[1]), sys.argv[2]
source = root / 'synthetic.log'
def generator(raw, output, base, chunk, top):
    output.mkdir(parents=True)
    (output / 'index.html').write_text('synthetic', encoding='utf-8')
    (output / 'data').mkdir()
    (output / 'data' / 'catalog.js').write_text('synthetic', encoding='utf-8')
    (output / 'data' / 'raw_0000.js').write_text('raw', encoding='utf-8')
    return {'events': 1, 'physical_lines': 1}
if phase == 'after_report_rename':
    original = Path.rename
    def exit_after_rename(path, target):
        result = original(path, target)
        if path.name.endswith('.building'):
            os._exit(71)
        return result
    Path.rename = exit_after_rename
elif phase == 'after_inventory_save':
    original = akuz_app.save_store
    def exit_after_save(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(72)
    akuz_app.save_store = exit_after_save
elif phase == 'after_intent_creation':
    original = akuz_app.write_intent
    def exit_after_intent(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(70)
    akuz_app.write_intent = exit_after_intent
akuz_app._publish(root, load_store(root), 'synthetic-key', source,
                  None, [], 'synthetic', 'single', gen_fn=generator)
raise RuntimeError('Crash hook was not reached')
"""

class ReportCrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='akuz_phase9_report_crash_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'synthetic.log'
        self.source.write_text('12:00:00.000,AKUZ,r,u: synthetic\n',
                               encoding='utf-8')
        self.store = load_store(self.root)
        self.store['downloads']['old'] = {'marker': 'old'}
        save_store(self.root, self.store)

    @staticmethod
    def generator(raw, output, base, chunk, top):
        output.mkdir(parents=True)
        (output / 'index.html').write_text('synthetic', encoding='utf-8')
        (output / 'data').mkdir()
        (output / 'data' / 'catalog.js').write_text('synthetic',
                                                  encoding='utf-8')
        (output / 'data' / 'raw_0000.js').write_text('raw', encoding='utf-8')
        return {'events': 1, 'physical_lines': 1}

    def crash(self, phase, exit_code):
        result = subprocess.run([sys.executable, '-B', '-c', CRASH_CHILD,
                                 str(self.root), phase], cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, exit_code, result.stderr)

    def published_dirs(self):
        return set((self.root / 'reports').glob('v4_*'))

    def test_exit_after_report_rename_recovers_exact_report_on_retry(self):
        self.crash('after_report_rename', 71)
        old_dirs = self.published_dirs()
        self.assertEqual(len(old_dirs), 1)
        self.assertEqual(load_store(self.root)['reports'], {})
        self.assertEqual(set(load_store(self.root)['downloads']), {'old'})
        value = akuz_app._publish(self.root, load_store(self.root),
            'synthetic-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertTrue(value['reused'])
        self.assertEqual(len(load_store(self.root)['reports']), 1)
        self.assertEqual(self.published_dirs(), old_dirs)
        self.assertFalse(list((self.root/'cache'/'report_intents').glob('*.json')))

    def test_exit_after_inventory_save_reuses_published_report(self):
        self.crash('after_inventory_save', 72)
        before = self.published_dirs()
        self.assertEqual(len(before), 1)
        indexed = load_store(self.root)['reports']
        self.assertEqual(len(indexed), 1)
        value = akuz_app._publish(self.root, load_store(self.root),
            'synthetic-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertTrue(value['reused'])
        self.assertEqual(before, self.published_dirs())
        self.assertEqual(len(load_store(self.root)['reports']), 1)
        self.assertFalse(list((self.root/'cache'/'report_intents').glob('*.json')))


    def test_exit_after_intent_does_not_publish_incomplete_staging(self):
        self.crash('after_intent_creation', 70)
        staging = list((self.root/'reports').glob('*.building'))
        self.assertEqual(len(staging), 1)
        self.assertEqual(load_store(self.root)['reports'], {})
        result = akuz_app._publish(self.root, load_store(self.root),
            'synthetic-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertFalse(result['reused'])
        self.assertEqual(len(load_store(self.root)['reports']), 1)
        self.assertTrue(staging[0].exists())  # No global cleanup.

    def test_other_cache_key_cannot_adopt_interrupted_report(self):
        self.crash('after_report_rename', 71)
        original = self.published_dirs()
        different = akuz_app._publish(self.root, load_store(self.root),
            'another-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertFalse(different['reused'])
        self.assertEqual(len(self.published_dirs()), 2)
        self.assertTrue(original.issubset(self.published_dirs()))
        recovered = akuz_app._publish(self.root, load_store(self.root),
            'synthetic-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertTrue(recovered['reused'])
        self.assertEqual(len(load_store(self.root)['reports']), 2)


    def test_corrupt_catalog_cannot_be_adopted_or_deleted(self):
        self.crash('after_report_rename', 71)
        original = next(iter(self.published_dirs()))
        (original/'data'/'catalog.js').write_text('corrupted', encoding='utf-8')
        result = akuz_app._publish(self.root, load_store(self.root),
            'synthetic-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertFalse(result['reused'])
        self.assertTrue(original.exists())
        self.assertEqual(len(self.published_dirs()), 2)
        self.assertEqual(len(load_store(self.root)['reports']), 1)

    def test_forged_traversal_id_cannot_be_adopted_or_deleted(self):
        self.crash('after_report_rename', 71)
        original = next(iter(self.published_dirs()))
        marker = next((self.root/'cache'/'report_intents').glob('*.json'))
        payload = json.loads(marker.read_text('utf-8'))
        payload['value']['id'] = '../unowned'
        marker.write_text(json.dumps(payload), encoding='utf-8')
        result = akuz_app._publish(self.root, load_store(self.root),
            'synthetic-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertFalse(result['reused'])
        self.assertTrue(original.exists())
        self.assertTrue(marker.exists())
        self.assertEqual(len(self.published_dirs()), 2)


    def test_recovery_inventory_save_failure_remains_retryable(self):
        self.crash('after_report_rename', 71)
        original = self.published_dirs()
        inventory = self.root/'cache'/'inventory.json'
        old_bytes = inventory.read_bytes()
        with patch('akuz_publication.save_store',
                   side_effect=OSError('recovery inventory failure')):
            with self.assertRaisesRegex(OSError, 'recovery inventory failure'):
                akuz_app._publish(self.root, load_store(self.root),
                    'synthetic-key', self.source, None, [], 'synthetic',
                    'single', gen_fn=self.generator)
        self.assertEqual(inventory.read_bytes(), old_bytes)
        self.assertEqual(original, self.published_dirs())
        self.assertEqual(len(list((self.root/'cache'/'report_intents').glob('*.json'))), 1)
        result = akuz_app._publish(self.root, load_store(self.root),
            'synthetic-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertTrue(result['reused'])
        self.assertEqual(original, self.published_dirs())

    def test_directory_rename_failure_removes_owned_intent_and_staging(self):
        with patch.object(Path, 'rename',
                          side_effect=OSError('rename permission failure')):
            with self.assertRaisesRegex(OSError, 'rename permission failure'):
                akuz_app._publish(self.root, self.store, 'synthetic-key',
                    self.source, None, [], 'synthetic', 'single',
                    gen_fn=self.generator)
        self.assertFalse(list((self.root/'cache'/'report_intents').glob('*.json')))
        self.assertFalse(list((self.root/'reports').glob('*.building')))
        self.assertFalse(self.published_dirs())


    def test_missing_raw_shard_fails_closed_and_keeps_unindexed_dir(self):
        self.crash('after_report_rename', 71)
        original = next(iter(self.published_dirs()))
        (original/'data'/'raw_0000.js').unlink()
        result = akuz_app._publish(self.root, load_store(self.root),
            'synthetic-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertFalse(result['reused'])
        self.assertTrue(original.exists())
        self.assertEqual(len(self.published_dirs()), 2)
        self.assertEqual(len(load_store(self.root)['reports']), 1)


    def test_corrupt_intent_is_not_adopted_or_deleted(self):
        self.crash('after_report_rename', 71)
        original = next(iter(self.published_dirs()))
        marker = next((self.root/'cache'/'report_intents').glob('*.json'))
        marker.write_text('{not valid JSON', encoding='utf-8')
        result = akuz_app._publish(self.root, load_store(self.root),
            'synthetic-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertFalse(result['reused'])
        self.assertTrue(original.exists())
        self.assertTrue(marker.exists())
        self.assertEqual(len(self.published_dirs()), 2)

    def test_intent_replace_failure_leaves_no_published_report(self):
        with patch.object(Path, 'replace',
                          side_effect=OSError('intent replace failure')):
            with self.assertRaisesRegex(OSError, 'intent replace failure'):
                akuz_app._publish(self.root, self.store, 'synthetic-key',
                    self.source, None, [], 'synthetic', 'single',
                    gen_fn=self.generator)
        self.assertFalse(self.published_dirs())
        self.assertFalse(list((self.root/'reports').glob('*.building')))
        self.assertFalse(list((self.root/'cache'/'report_intents').glob('*')))
        self.assertEqual(load_store(self.root)['reports'], {})


if __name__ == '__main__':
    unittest.main()
