"""Synthetic same-root two-writer inventory race probe.

Reproduces the CURRENT bug, not a multiwriter-safety acceptance test.
No live app data, external network, SSH configuration or real logs.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
from time import monotonic, sleep
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akuz_store import load_store, save_store

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
    child = subprocess.Popen([sys.executable, '-B', '-c', CHILD, str(root)],
                             cwd=Path(__file__).resolve().parents[1],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    try:
        deadline=monotonic()+20
        while not (root/'ready').exists() and monotonic()<deadline:
            if child.poll() is not None:
                raise RuntimeError('Child exited before temporary write')
            sleep(.01)
        if not (root/'ready').exists():
            raise TimeoutError('Did not reach the controlled race boundary')
        other=load_store(root)
        other['downloads']['B'] = {'marker':'B'}
        save_store(root,other)
        (root/'resume').write_text('go',encoding='ascii')
        _,stderr=child.communicate(timeout=20)
        seen=load_store(root)['downloads']
        correct=(set(seen)=={'initial','A','B'} and child.returncode==0)
        if correct:
            print('MULTIWRITER_RACE_NOT_REPRODUCED',seen.keys())
        else:
            assert seen=={'initial':{'marker':'original'},
                          'B':{'marker':'B'}},seen
            assert child.returncode!=0 and 'FileNotFoundError' in stderr
            assert not (root/'cache'/'inventory.json.tmp').exists()
            print('MULTIWRITER_LOST_UPDATE_REPRODUCED',
                  'A_write_failed=True', 'persisted_A=False',
                  'persisted_B=True', 'initial_preserved=True')
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate()
