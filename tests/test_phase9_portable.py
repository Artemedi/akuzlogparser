"""Phase 9.0b: portable ZIP metadata and sanitized, child-only OS metrics."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import unittest
import zipfile

from scripts.bench_phase9_portable import EXPECTED, manifest


class PortablePhase9Tests(unittest.TestCase):
    def test_package_allowlist_and_hashes(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "synthetic-portable.zip"
            with zipfile.ZipFile(archive, "w") as pack:
                for name in sorted(EXPECTED):
                    content = (json.dumps(dict(version="synthetic", commit="test-sha",
                        python="test-python")) if name.endswith("BUILD_INFO.json")
                        else "synthetic-exe" if name.endswith(".exe")
                        else "synthetic-config-example")
                    pack.writestr(name, content)
            result = manifest(archive)
            self.assertEqual(result["build_git_sha"], "test-sha")
            self.assertEqual(result["version"], "synthetic")
            self.assertEqual(result["exe_sha256"],
                             hashlib.sha256(b"synthetic-exe").hexdigest())
            self.assertEqual(result["zip_sha256"],
                             hashlib.sha256(archive.read_bytes()).hexdigest())
            with zipfile.ZipFile(archive, "a") as pack:
                pack.writestr("AKUZLogExplorer/private.log", "synthetic secret")
            with self.assertRaisesRegex(ValueError, "allowlist"):
                manifest(archive)


if __name__ == "__main__":
    unittest.main()
