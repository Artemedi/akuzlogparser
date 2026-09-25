"""Performance trace should be bounded, private and compatible with Windows cleanup."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from akuz_diagnostics import event, phase


class DiagnosticsTests(unittest.TestCase):
    def test_elapsed_failure_and_no_raw_fields(self):
        with TemporaryDirectory() as scratch:
            root=Path(scratch)
            event(root, "synthetic.stage", "progress", events=50000,
                  unsafe_name="patient-name", file_path="C:/secret")
            with phase(root, "synthetic.phase", events=2):
                pass
            with self.assertRaises(ValueError):
                with phase(root, "synthetic.failed"):
                    raise ValueError("secret patient text")
            path=root/"diagnostics"/"performance.txt"
            content=path.read_text(encoding="utf-8")
            self.assertIn("events=50000",content)
            self.assertIn("stage=synthetic.phase status=done",content)
            self.assertIn("stage=synthetic.failed status=failed",content)
            self.assertIn("elapsed_s=",content)
            self.assertNotIn("patient-name",content)
            self.assertNotIn("secret patient text",content)
            self.assertNotIn("C:/secret",content)


if __name__=="__main__":
    unittest.main()
