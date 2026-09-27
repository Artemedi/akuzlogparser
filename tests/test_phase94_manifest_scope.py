"""The Phase 9.4 manifest may normalize only known volatile fields."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.bench_phase9_baseline import file_manifest


class Phase94ManifestScopeTests(unittest.TestCase):
    def test_only_generated_provenance_field_is_ignored(self):
        with TemporaryDirectory() as td:
            a,b=(Path(td)/x for x in ('a','b'))
            a.mkdir(); b.mkdir()
            common={'source':{'host':'test-host','date':'2026-09-24'},
                    'events':42,'generated':'time-one'}
            (a/'provenance.json').write_text(json.dumps(common),encoding='utf8')
            common['generated']='time-two'
            (b/'provenance.json').write_text(json.dumps(common),encoding='utf8')
            self.assertEqual(file_manifest(a),file_manifest(b))
            common['source']['host']='different-host'
            (b/'provenance.json').write_text(json.dumps(common),encoding='utf8')
            self.assertNotEqual(file_manifest(a),file_manifest(b))

    def test_one_temporary_catalog_source_is_normalized_not_all(self):
        with TemporaryDirectory() as td:
            a,b=(Path(td)/x for x in ('a','b'))
            for root in (a,b): (root/'data').mkdir(parents=True)
            rel=Path('data/catalog.js')
            (a/rel).write_bytes(b'{"source":"akuz-v4-merge-one.jsonl"}')
            (b/rel).write_bytes(b'{"source":"akuz-v4-merge-two.jsonl"}')
            self.assertEqual(file_manifest(a),file_manifest(b))
            (a/rel).write_bytes(
                b'{"source":"akuz-v4-merge-one.jsonl","nested":{"source":"akuz-v4-merge-extra.jsonl"}}')
            (b/rel).write_bytes(
                b'{"source":"akuz-v4-merge-two.jsonl","nested":{"source":"akuz-v4-merge-changed.jsonl"}}')
            self.assertNotEqual(file_manifest(a),file_manifest(b))

    def test_raw_shard_is_hashed_without_normalization(self):
        with TemporaryDirectory() as td:
            a,b=(Path(td)/x for x in ('a','b'))
            for root in (a,b):
                (root/'data').mkdir(parents=True)
                (root/'data/raw_000.js').write_bytes(b'const row=1;\n')
            self.assertEqual(file_manifest(a),file_manifest(b))
            (b/'data/raw_000.js').write_bytes(b'const row=2;\n')
            self.assertNotEqual(file_manifest(a),file_manifest(b))


if __name__=='__main__':
    unittest.main()
