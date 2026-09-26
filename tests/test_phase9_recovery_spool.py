"""Recovered single report must not replay an empty derived spool."""
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from akuz_store import load_store
from scripts.bench_phase9_baseline import create_sources, inventory_manifest
from tests.test_phase9_spool import build

ROOT = Path(__file__).resolve().parents[1]
CRASH_ON_FIRST_REPORT = """
import os
import sys
from pathlib import Path
from akuz_app import State, perform_build_current, perform_list
root, sources = Path(sys.argv[1]), Path(sys.argv[2])
state = State()
perform_list(root, state, source='local', local_path=str(sources))
selected = [dict(id=x['id'], date='') for x in
            sorted(state.listing, key=lambda x:x['name'])]
original = Path.rename
def exit_after_first_report(path, target):
    result = original(path, target)
    if path.name.endswith('.building'):
        os._exit(79)
    return result
Path.rename = exit_after_first_report
perform_build_current(root, state, selected, use_derived_spool=True)
raise RuntimeError('Expected crash after first report rename')
"""


class RecoveredSpoolTests(unittest.TestCase):
    def test_recovered_single_and_fresh_singles_make_same_combined(self):
        with TemporaryDirectory(prefix='akuz_phase9_spool_recover_') as td:
            home = Path(td)
            sources = home/'sources'
            create_sources(sources, 7, 120)
            _, reference = build(home/'control', sources, False)
            actual = home/'recovery'
            child = subprocess.run([sys.executable, '-B', '-c',
                CRASH_ON_FIRST_REPORT, str(actual), str(sources)],
                cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(child.returncode, 79, child.stderr)
            self.assertEqual(load_store(actual)['reports'], {})
            previous = set((actual/'reports').glob('v4_*'))
            self.assertEqual(len(previous), 1)
            result, candidate = build(actual, sources, True)
            self.assertFalse(result['reused'])  # Combined is fresh.
            self.assertTrue(result['reports'][0]['reused'])
            self.assertEqual(len(result['reports']), 3)
            self.assertEqual(reference, candidate)
            self.assertTrue(previous.issubset(set((actual/'reports').glob('v4_*'))))
            self.assertEqual(len(load_store(actual)['reports']), 4)
            self.assertFalse(list((actual/'cache'/'report_intents').glob('*.json')))


if __name__ == '__main__':
    unittest.main()
