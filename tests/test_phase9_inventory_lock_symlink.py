"""Reject preexisting symlinked inventory ownership paths."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import os
import subprocess

from akuz_instance_lock import exclusive_instance
from akuz_store_lock import inventory_transaction


class InventoryLockSymlinkTests(unittest.TestCase):
    def symlink(self, path, target, directory):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest('OS does not permit symlinks: ' + str(exc))

    def test_reject_symlinked_cache_directory(self):
        with TemporaryDirectory(prefix='akuz_guard_cache_') as td:
            parent=Path(td)
            root=parent/'app'
            root.mkdir()
            outside=parent/'outside'
            outside.mkdir()
            self.symlink(root/'cache', outside, True)
            with self.assertRaisesRegex(OSError, 'redirected'):
                with inventory_transaction(root):
                    self.fail('symlinked cache accepted')
            self.assertFalse((outside/'inventory.lock').exists())

    def test_reject_symlinked_lock_file(self):
        with TemporaryDirectory(prefix='akuz_guard_file_') as td:
            parent=Path(td)
            root=parent/'app'
            cache=root/'cache'
            cache.mkdir(parents=True)
            outside=parent/'outside.lock'
            outside.write_bytes(b'owned')
            self.symlink(cache/'inventory.lock', outside, False)
            with self.assertRaisesRegex(OSError, 'redirected'):
                with inventory_transaction(root):
                    self.fail('symlinked lock accepted')
            self.assertEqual(outside.read_bytes(), b'owned')


    @unittest.skipUnless(os.name == 'nt', 'NTFS junction test requires Windows')
    def test_reject_windows_directory_junction_for_both_locks(self):
        with TemporaryDirectory(prefix='akuz_guard_junction_') as td:
            parent=Path(td)
            root=parent/'app'
            root.mkdir()
            outside=parent/'outside'
            outside.mkdir()
            link=root/'cache'
            result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],
                                  capture_output=True,text=True)
            if result.returncode:
                self.skipTest('Junction creation unavailable')
            try:
                self.assertFalse(link.is_symlink())
                with self.assertRaisesRegex(OSError, 'redirected'):
                    with inventory_transaction(root):
                        self.fail('NTFS junction accepted for inventory')
                with self.assertRaisesRegex(OSError, 'redirected'):
                    with exclusive_instance(root):
                        self.fail('NTFS junction accepted for server')
                self.assertFalse((outside/'inventory.lock').exists())
                self.assertFalse((outside/'.akuz-instance.lock').exists())
            finally:
                link.rmdir()


if __name__ == '__main__':
    unittest.main()
