"""Windows/process crash boundaries of synthetic v4 inventory persistence.

Only a disposable child interpreter is terminated, never the test runner.
"""
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from akuz_store import load_store, save_store


ROOT = Path(__file__).resolve().parents[1]
CRASH_CHILD = """
import os
import sys
from pathlib import Path
from akuz_store import load_store, save_store
root, phase = Path(sys.argv[1]), sys.argv[2]
original_replace = Path.replace
def injected_replace(path, destination):
    if path.name == 'inventory.json.tmp':
        if phase == 'before':
            os._exit(61)
        result = original_replace(path, destination)
        if phase == 'after':
            os._exit(62)
        return result
    return original_replace(path, destination)
Path.replace = injected_replace
value = load_store(root)
value['downloads']['new'] = {'marker': 'new'}
save_store(root, value)
raise RuntimeError('crash hook was not reached')
"""

class InventoryCrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='akuz_phase9_crash_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        store = load_store(self.root)
        store['downloads']['old'] = {'marker': 'old'}
        save_store(self.root, store)
        self.inventory = self.root / 'cache' / 'inventory.json'
        self.tmp = self.inventory.with_name('inventory.json.tmp')

    def crash(self, phase, expected_exit):
        proc = subprocess.run(
            [sys.executable, '-B', '-c', CRASH_CHILD, str(self.root), phase],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=15)
        self.assertEqual(proc.returncode, expected_exit,
                         'Child failed before injected crash: ' + proc.stderr)

    def test_exit_before_replace_preserves_old_inventory_and_recovers(self):
        old_bytes = self.inventory.read_bytes()
        self.crash('before', 61)
        self.assertEqual(self.inventory.read_bytes(), old_bytes)
        self.assertTrue(self.tmp.exists())  # os._exit cannot run cleanup.
        self.assertEqual(set(load_store(self.root)['downloads']), {'old'})
        recovered = load_store(self.root)
        recovered['downloads']['recovered'] = {'marker': 'recovered'}
        save_store(self.root, recovered)
        self.assertFalse(self.tmp.exists())
        self.assertEqual(set(load_store(self.root)['downloads']),
                         {'old', 'recovered'})

    def test_exit_after_replace_keeps_new_inventory_without_temp(self):
        old_bytes = self.inventory.read_bytes()
        self.crash('after', 62)
        self.assertNotEqual(self.inventory.read_bytes(), old_bytes)
        self.assertFalse(self.tmp.exists())
        self.assertEqual(set(load_store(self.root)['downloads']),
                         {'old', 'new'})
        value = load_store(self.root)
        value['downloads']['recovered'] = {'marker': 'recovered'}
        save_store(self.root, value)
        self.assertFalse(self.tmp.exists())
        self.assertEqual(set(load_store(self.root)['downloads']),
                         {'old', 'new', 'recovered'})


if __name__ == '__main__':
    unittest.main()
