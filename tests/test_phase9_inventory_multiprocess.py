"""Cross-process inventory transaction and optimistic conflict gates."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest

from akuz_store import load_store, save_store
from akuz_store_lock import (
    InventoryBusyError, InventoryConflictError, inventory_transaction,
)

ROOT = Path(__file__).resolve().parents[1]

HOLDER = r"""
import sys
from pathlib import Path
from time import monotonic, sleep
from akuz_store_lock import inventory_transaction
root=Path(sys.argv[1])
mode=sys.argv[2]
with inventory_transaction(root):
    (root/'ready').write_text('ready',encoding='ascii')
    if mode == 'crash':
        import os
        os._exit(73)
    deadline=monotonic()+20
    while not (root/'resume').exists() and monotonic()<deadline:
        sleep(.01)
    if not (root/'resume').exists():
        raise TimeoutError('parent did not resume')
"""


class InventoryMultiprocessTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='akuz_inventory_mp_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state = load_store(self.root)
        state['downloads']['initial'] = {'marker': 'initial'}
        save_store(self.root, state)

    def child(self, mode):
        return subprocess.Popen(
            [sys.executable, '-B', '-c', HOLDER, str(self.root), mode],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)

    def wait_ready(self, child):
        deadline = monotonic()+20
        while not (self.root/'ready').exists() and monotonic()<deadline:
            if child.poll() is not None:
                out, err = child.communicate()
                self.fail(f'child exited early {child.returncode}: {out} {err}')
            sleep(.01)
        self.assertTrue((self.root/'ready').exists(), 'lock holder not ready')

    def test_other_process_gets_busy_then_can_retry(self):
        child = self.child('wait')
        try:
            self.wait_ready(child)
            with self.assertRaises(InventoryBusyError):
                with inventory_transaction(self.root):
                    self.fail('busy transaction unexpectedly entered')
            (self.root/'resume').write_text('go', encoding='ascii')
            out, err = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, (out, err))
            with inventory_transaction(self.root):
                current = load_store(self.root)
                current['downloads']['after'] = {'marker': 'after'}
                save_store(self.root, current)
            self.assertIn('after', load_store(self.root)['downloads'])
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()

    def test_process_exit_releases_os_lock(self):
        child = self.child('crash')
        self.wait_ready(child)
        out, err = child.communicate(timeout=20)
        self.assertEqual(child.returncode, 73, (out, err))
        with inventory_transaction(self.root):
            current = load_store(self.root)
            current['downloads']['recovered'] = {'marker': 'recovered'}
            save_store(self.root, current)
        self.assertIn('recovered', load_store(self.root)['downloads'])

    def test_stale_snapshot_fails_closed_instead_of_lost_update(self):
        first = load_store(self.root)
        stale = load_store(self.root)
        first['downloads']['A'] = {'marker': 'A'}
        save_store(self.root, first)
        stale['downloads']['B'] = {'marker': 'B'}
        with self.assertRaises(InventoryConflictError):
            save_store(self.root, stale)
        persisted = load_store(self.root)
        self.assertEqual(set(persisted['downloads']), {'initial', 'A'})
        retry = load_store(self.root)
        retry['downloads']['B'] = {'marker': 'B'}
        save_store(self.root, retry)
        self.assertEqual(set(load_store(self.root)['downloads']),
                         {'initial', 'A', 'B'})


if __name__ == '__main__':
    unittest.main()

# Entry-point lock behavior is deliberately tested after the low-level cases.
class InventoryEntrypointLockTests(InventoryMultiprocessTests):
    def test_mutating_entrypoints_fail_fast_while_other_process_owns_root(self):
        from akuz_app import State, perform_build, perform_clear
        from akuz_analytics import update_source_date

        child = self.child('wait')
        try:
            self.wait_ready(child)
            with self.assertRaises(InventoryBusyError):
                perform_build(self.root, State(), [])
            with self.assertRaises(InventoryBusyError):
                perform_clear(self.root, State(), False)
            with self.assertRaises(InventoryBusyError):
                update_source_date(self.root, '0' * 64, '')
            (self.root/'resume').write_text('go', encoding='ascii')
            out, err = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, (out, err))
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
