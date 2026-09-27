"""Fable review reproducible OPEN reliability gaps, not acceptance passes.

Expected failures must be removed only after the matching behavior is fixed.
All files are synthetic and in isolated temporary directories.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from akuz_app import _publish
from akuz_store import load_store
from tests import test_phase9_report_crash as report_crash_fixture


class ReviewedOpenLimits(unittest.TestCase):
    def test_indexed_report_reuse_rejects_corrupt_catalog(self):
        with TemporaryDirectory(prefix='akuz_review_indexed_') as td:
            root = Path(td)
            source = root / 'synthetic.log'
            source.write_text('synthetic', encoding='utf-8')
            report = _publish(root, load_store(root), 'synthetic-key',
                source, None, [], 'synthetic', 'single',
                gen_fn=report_crash_fixture.ReportCrashTests.generator)
            (root/'reports'/report['id']/'data'/'catalog.js').write_text(
                'CORRUPTED', encoding='utf-8')
            again = _publish(root, load_store(root), 'synthetic-key',
                source, None, [], 'synthetic', 'single',
                gen_fn=report_crash_fixture.ReportCrashTests.generator)
            self.assertFalse(again['reused'], 'Damaged indexed catalog must regenerate')

    def test_crash_recovery_rejects_same_size_raw_corruption(self):
        fixture = report_crash_fixture.ReportCrashTests()
        fixture.setUp()
        try:
            fixture.crash('after_report_rename', 71)
            original = next(iter(fixture.published_dirs()))
            shard = original/'data'/'raw_0000.js'
            contents = shard.read_bytes()
            shard.write_bytes(b'X' * len(contents))
            recovered = _publish(fixture.root, load_store(fixture.root),
                'synthetic-key', fixture.source, None, [], 'synthetic',
                'single', gen_fn=fixture.generator)
            self.assertFalse(recovered['reused'],
                'Same-size raw corruption must not be recovered')
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
