"""Verify that busy and broken OS handles are distinguishable."""
import errno
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from akuz_instance_lock import InstanceBusy, exclusive_instance
from akuz_store_lock import _root_lock


class LockErrorTests(unittest.TestCase):
    def test_non_busy_oserror_propagates_and_owner_is_reusable(self):
        backend = 'msvcrt.locking' if os.name == 'nt' else 'fcntl.flock'
        with TemporaryDirectory(prefix='akuz_lock_errno_') as td:
            root = Path(td)
            with patch(backend, side_effect=OSError(errno.EBADF, 'bad fd')):
                with self.assertRaises(OSError) as caught:
                    with exclusive_instance(root):
                        self.fail('bad fd acquired lock')
            self.assertEqual(caught.exception.errno, errno.EBADF)
            self.assertNotIsInstance(caught.exception, InstanceBusy)
            with exclusive_instance(root):
                self.assertTrue((root/'cache'/'.akuz-instance.lock').is_file())

    def test_busy_errno_maps_to_instance_busy(self):
        backend = 'msvcrt.locking' if os.name == 'nt' else 'fcntl.flock'
        with TemporaryDirectory(prefix='akuz_lock_busy_') as td:
            with patch(backend, side_effect=OSError(errno.EACCES, 'busy')):
                with self.assertRaises(InstanceBusy):
                    with exclusive_instance(Path(td)):
                        self.fail('busy fd acquired lock')

    @unittest.skipUnless(os.name == 'nt', 'Windows case-insensitive NTFS')
    def test_alternate_path_case_reuses_local_inventory_mutex(self):
        with TemporaryDirectory(prefix='akuz_inventory_case_') as td:
            original = Path(td)
            second = Path(str(original).swapcase())
            self.assertIs(_root_lock(original), _root_lock(second))


if __name__ == '__main__':
    unittest.main()
