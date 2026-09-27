"""Windows frozen parity cleanup guards; synthetic disposable directories only."""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts import bench_phase9_frozen_real as bench


class FrozenCleanupTests(unittest.TestCase):
    def _new_home(self, diag):
        home = Path(tempfile.mkdtemp(prefix="phase9_frozen_", dir=diag))
        (home / bench.MARKER).write_text("disposable\n", encoding="ascii")
        (home / "frozen").mkdir()
        (home / "frozen" / "AKUZLogExplorer.exe").write_bytes(b"synthetic")
        return home

    def test_own_workspace_removed_other_workspace_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            diag = Path(td) / "diagnostics"
            diag.mkdir()
            home = self._new_home(diag)
            active = diag / "phase9_frozen_other_active"
            active.mkdir()
            sentinel = active / "active.part"
            sentinel.write_bytes(b"other run's synthetic data")
            with patch.object(bench, "DIAG", diag):
                bench.cleanup_owned_workspace(home)
            self.assertFalse(home.exists())
            self.assertEqual(sentinel.read_bytes(), b"other run's synthetic data")

    def test_missing_marker_refuses_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            diag = Path(td) / "diagnostics"
            diag.mkdir()
            home = diag / "phase9_frozen_unowned"
            home.mkdir()
            sentinel = home / "keep.txt"
            sentinel.write_text("KEEP", encoding="ascii")
            with patch.object(bench, "DIAG", diag):
                with self.assertRaisesRegex(RuntimeError, "Refusing cleanup"):
                    bench.cleanup_owned_workspace(home)
            self.assertEqual(sentinel.read_text(encoding="ascii"), "KEEP")

    def test_retry_after_partial_cleanup_removed_marker(self):
        with tempfile.TemporaryDirectory() as td:
            diag = Path(td) / "diagnostics"
            diag.mkdir()
            home = self._new_home(diag)
            real_rmtree = shutil.rmtree
            attempts = []

            def flaky(path):
                attempts.append(path)
                if len(attempts) == 1:
                    # rmtree may already have removed ownership marker.
                    (home / bench.MARKER).unlink()
                    lock_error = PermissionError(13, "synthetic exe lock", str(path), 5)
                    # On Linux the fourth ctor argument does not expose
                    # Windows winerror: emulate the real API explicitly.
                    lock_error.winerror = 5
                    raise lock_error
                real_rmtree(path)

            with patch.object(bench, "DIAG", diag), \
                 patch.object(bench.shutil, "rmtree", side_effect=flaky), \
                 patch.object(bench, "sleep"):
                bench.cleanup_owned_workspace(home)
            self.assertEqual(attempts, [home, home])
            self.assertFalse(home.exists())

    def test_unrecognized_error_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as td:
            diag = Path(td) / "diagnostics"
            diag.mkdir()
            home = self._new_home(diag)
            with patch.object(bench, "DIAG", diag), \
                 patch.object(bench.shutil, "rmtree",
                              side_effect=PermissionError("synthetic ACL failure")):
                with self.assertRaises(PermissionError):
                    bench.cleanup_owned_workspace(home)
            self.assertTrue(home.exists())


if __name__ == "__main__":
    unittest.main()
