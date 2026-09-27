"""Synthetic same-root two-writer inventory acceptance probe.

Historically this reproduced a fixed inventory.json.tmp collision and lost
update. Current expected result: writer B fails fast while A owns the lock,
then reloads and succeeds after A commits. No live app data or real logs.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
from time import monotonic, sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akuz_store import load_store, save_store
from akuz_store_lock import InventoryBusyError

CHILD = r"""
import sys
from pathlib import Path
from time import monotonic, sleep
from akuz_store import load_store, save_store
root = Path(sys.argv[1])
ready, resume = root/'ready', root/'resume'
state = load_store(root)
state['downloads']['A'] = {'marker':'A'}
original = Path.replace
def pause_before_replace(self, target):
    if self.name == 'inventory.json.tmp':
        ready.write_text('ready', encoding='ascii')
        until = monotonic()+20
        while not resume.exists() and monotonic()<until:
            sleep(.01)
        if not resume.exists():
            raise TimeoutError('Synthetic parent did not resume')
    return original(self,target)
Path.replace = pause_before_replace
save_store(root,state)
"""

with TemporaryDirectory(prefix='akuz_phase9_multiwriter_') as td:
    root = Path(td)
    old = load_store(root)
    old['downloads']['initial'] = {'marker': 'original'}
    save_store(root, old)
    child = subprocess.Popen(
        [sys.executable, '-B', '-c', CHILD, str(root)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline=monotonic()+20
        while not (root/'ready').exists() and monotonic()<deadline:
            if child.poll() is not None:
                raise RuntimeError('Child exited before controlled boundary')
            sleep(.01)
        if not (root/'ready').exists():
            raise TimeoutError('Did not reach controlled race boundary')

        stale = load_store(root)
        stale['downloads']['B'] = {'marker':'B'}
        try:
            save_store(root, stale)
        except InventoryBusyError:
            busy = True
        else:
            busy = False
        if not busy:
            raise AssertionError('Second writer did not fail fast while lock held')

        (root/'resume').write_text('go', encoding='ascii')
        out, err = child.communicate(timeout=20)
        if child.returncode:
            raise RuntimeError(f'First writer failed: {out} {err}')

        retry = load_store(root)
        retry['downloads']['B'] = {'marker':'B'}
        save_store(root, retry)
        seen = load_store(root)['downloads']
        assert set(seen) == {'initial','A','B'}, seen
        assert not (root/'cache'/'inventory.json.tmp').exists()
        print('MULTIWRITER_LOCK_PASS',
              'second_writer_busy=True',
              'persisted_A=True', 'persisted_B=True',
              'initial_preserved=True')
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate()
