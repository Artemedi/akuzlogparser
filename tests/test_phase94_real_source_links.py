"""Read-only, exact-date local source binding for isolated Phase 9.4 runs."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from akuz_store import sha256
from scripts.bench_phase94_combined_only import REAL_DAYS, link_real_sources


class RealSourceLinkTests(unittest.TestCase):
    def make_downloads(self, root):
        incoming=root/'downloads'
        incoming.mkdir()
        for day in REAL_DAYS:
            (incoming/('akuz_v4_test_'+day+'_server.log')).write_bytes(
                ('fixture '+day+'\n').encode('ascii'))
        return incoming

    def test_links_exact_three_preserve_original_and_sha(self):
        with TemporaryDirectory() as td:
            root=Path(td)
            incoming=self.make_downloads(root)
            before={p.name:sha256(p) for p in incoming.iterdir()}
            evidence,originals=link_real_sources(incoming,root/'owned')
            self.assertEqual([x['date'] for x in evidence],list(REAL_DAYS))
            for entry in evidence:
                linked=root/'owned'/(entry['date']+'_server.log')
                self.assertTrue(os.path.samefile(linked,
                    originals[entry['date']]))
                self.assertEqual(entry['sha256'],sha256(linked))
            for linked in (root/'owned').iterdir():
                linked.unlink()
            self.assertEqual({p.name:sha256(p) for p in incoming.iterdir()},
                             before)

    def test_duplicate_day_fails_without_owning_other_files(self):
        with TemporaryDirectory() as td:
            root=Path(td)
            incoming=self.make_downloads(root)
            extra=incoming/'akuz_v4_other_20260923_server.log'
            extra.write_bytes(b'duplicate')
            with self.assertRaisesRegex(ValueError,'duplicate date'):
                link_real_sources(incoming,root/'owned')
            self.assertFalse((root/'owned').exists())
            self.assertEqual(extra.read_bytes(),b'duplicate')

    def test_missing_day_and_symlink_folder_fail_closed(self):
        with TemporaryDirectory() as td:
            root=Path(td)
            incoming=self.make_downloads(root)
            (incoming/'akuz_v4_test_20260925_server.log').unlink()
            with self.assertRaisesRegex(ValueError,'unique local'):
                link_real_sources(incoming,root/'owned')
            self.assertFalse((root/'owned').exists())


if __name__=='__main__':
    unittest.main()
