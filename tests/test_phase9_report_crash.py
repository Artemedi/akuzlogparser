"""Document observable report publication state after synthetic process exit."""
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

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
        return {'events': 1, 'physical_lines': 1}

    def crash(self, phase, exit_code):
        result = subprocess.run([sys.executable, '-B', '-c', CRASH_CHILD,
                                 str(self.root), phase], cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, exit_code, result.stderr)

    def published_dirs(self):
        return set((self.root / 'reports').glob('v4_*'))

    def test_exit_after_report_rename_exposes_unindexed_orphan_on_retry(self):
        self.crash('after_report_rename', 71)
        old_dirs = self.published_dirs()
        self.assertEqual(len(old_dirs), 1)
        self.assertEqual(load_store(self.root)['reports'], {})
        self.assertEqual(set(load_store(self.root)['downloads']), {'old'})
        value = akuz_app._publish(self.root, load_store(self.root),
            'synthetic-key', self.source, None, [], 'synthetic', 'single',
            gen_fn=self.generator)
        self.assertFalse(value['reused'])
        self.assertEqual(len(load_store(self.root)['reports']), 1)
        self.assertEqual(len(self.published_dirs()), 2)
        self.assertTrue(old_dirs.issubset(self.published_dirs()))

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


if __name__ == '__main__':
    unittest.main()
