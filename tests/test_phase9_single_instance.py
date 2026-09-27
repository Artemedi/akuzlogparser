"""Supported AKUZ app instances must not concurrently own the same cache."""
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest

from akuz_instance_lock import InstanceBusy, exclusive_instance

REPO = Path(__file__).resolve().parents[1]
SERVER = """
import sys
from pathlib import Path
import akuz_app
akuz_app.ROOT=Path(sys.argv[1])
sys.argv=['akuz_app.py','--no-browser','--port',sys.argv[2]]
akuz_app.main()
"""


def random_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def ready(proc, port):
    until=monotonic()+8
    while monotonic()<until:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(('127.0.0.1',port),.1):
                return True
        except OSError:
            sleep(.03)
    return False


class SingleInstanceTests(unittest.TestCase):
    def test_os_lock_is_crash_released_and_stable(self):
        with TemporaryDirectory(prefix='akuz_instance_') as td:
            root=Path(td)
            with exclusive_instance(root):
                with self.assertRaises(InstanceBusy):
                    with exclusive_instance(root):
                        self.fail('same-root second owner acquired lock')
                self.assertTrue((root/'cache'/'.akuz-instance.lock').is_file())
            with exclusive_instance(root):
                self.assertTrue((root/'cache'/'.akuz-instance.lock').is_file())

    def test_two_real_servers_different_ports_refuse_shared_root(self):
        with TemporaryDirectory(prefix='akuz_instance_servers_') as td:
            root=Path(td)
            one,two=random_port(),random_port()
            self.assertNotEqual(one,two)
            first=subprocess.Popen([sys.executable,'-B','-c',SERVER,
                str(root),str(one)],cwd=REPO,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            try:
                self.assertTrue(ready(first,one),'first app did not start')
                second=subprocess.run([sys.executable,'-B','-c',SERVER,
                    str(root),str(two)],cwd=REPO,text=True,
                    capture_output=True,timeout=12)
                self.assertEqual(second.returncode,2,second.stderr)
                self.assertIn('уже занят', second.stderr)
                self.assertTrue((root/'cache'/'.akuz-instance.lock').is_file())
            finally:
                first.kill()
                first.wait(timeout=10)
            # os.kill/process death leaves a persistent *unlocked* file.
            next_owner=subprocess.Popen([sys.executable,'-B','-c',SERVER,
                str(root),str(two)],cwd=REPO,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            try:
                self.assertTrue(ready(next_owner,two),
                                'crash did not release the app-root lock')
            finally:
                next_owner.kill()
                next_owner.wait(timeout=10)


    def test_failed_port_bind_releases_root_guard(self):
        with TemporaryDirectory(prefix='akuz_instance_bind_fail_') as td:
            root=Path(td)
            with socket.socket() as held:
                held.bind(('127.0.0.1',0))
                held.listen(1)
                port=held.getsockname()[1]
                result=subprocess.run([sys.executable,'-B','-c',SERVER,
                    str(root),str(port)],cwd=REPO,capture_output=True,
                    text=True,timeout=12)
            self.assertEqual(result.returncode,2,result.stderr)
            self.assertIn('Cannot start on 127.0.0.1', result.stderr)
            with exclusive_instance(root):
                self.assertTrue((root/'cache'/'.akuz-instance.lock').exists())


if __name__=='__main__':
    unittest.main()
