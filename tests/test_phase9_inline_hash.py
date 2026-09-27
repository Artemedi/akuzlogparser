"""Producer-hashed report bytes must equal existing text-write bytes."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from akuz_report_writer import write_report_text
from akuz_store import load_store, sha256
from akuz_app import _publish
from akuz_html_explorer import generate
from scripts.bench_phase9_baseline import create_sources


class InlineHashTests(unittest.TestCase):
    def test_native_text_writer_byte_equivalence(self):
        cases = ('', 'a\nb\r\nc\rd', 'Привет! 💾\r\nжурнал\n',
                 'a'*262143+'\r\nб\n'+'💥'*262144+'\n')
        with TemporaryDirectory(prefix='akuz_writer_') as td:
            root = Path(td)
            for n, value in enumerate(cases):
                reference = root/f'reference_{n}.js'
                candidate = root/f'candidate_{n}.js'
                reference.write_text(value, encoding='utf-8')
                digest, size = write_report_text(candidate, value)
                self.assertEqual(reference.read_bytes(), candidate.read_bytes())
                self.assertEqual(digest, sha256(candidate))
                self.assertEqual(size, candidate.stat().st_size)

    def test_generator_and_published_all_file_hashes(self):
        with TemporaryDirectory(prefix='akuz_writer_generator_') as td:
            root = Path(td)
            source_dir = root/'inputs'
            create_sources(source_dir, 25, 256)
            source = source_dir/'20260924_A.log'
            result = _publish(root, load_store(root), 'test-inline', source,
                              None, [], 'inline', 'single', gen_fn=generate)
            self.assertFalse(result['reused'])
            row = load_store(root)['reports'][result['id']]
            folder = root/'reports'/result['id']
            all_sha = row['integrity']['all_sha256']
            self.assertEqual(set(all_sha), set(row['integrity']['files']))
            for rel, digest in all_sha.items():
                self.assertEqual(digest, sha256(folder/rel))
            self.assertTrue(_publish(root, load_store(root),
                'test-inline', source, None, [], 'inline', 'single',
                gen_fn=generate)['reused'])

            trace = (root/'diagnostics'/'performance.txt').read_text('utf-8')
            producer_rows = [r for r in trace.splitlines()
                             if 'stage=report.integrity_hash status=done' in r]
            self.assertEqual(len(producer_rows), 1)
            self.assertIn('producer_files=', producer_rows[0])
            self.assertIn('input_bytes=', producer_rows[0])
            self.assertNotIn('producer_files=0 ', producer_rows[0])

    def test_small_chunks_and_surrogate_errors_match_text_mode(self):
        with TemporaryDirectory(prefix='akuz_writer_chunks_') as td:
            root = Path(td)
            text = 'я\r\nпривет\n💥\r\n\n'
            (root/'expected').write_text(text, encoding='utf-8')
            with patch('akuz_report_writer._CHARS', 2):
                digest, size = write_report_text(root/'actual', text)
            self.assertEqual((root/'actual').read_bytes(),
                             (root/'expected').read_bytes())
            self.assertEqual(size, (root/'actual').stat().st_size)
            self.assertEqual(digest, sha256(root/'actual'))
            with self.assertRaises(UnicodeEncodeError):
                write_report_text(root/'bad', 'invalid\ud800unicode')

    def test_custom_generator_does_not_bypass_full_hash_fallback(self):
        with TemporaryDirectory(prefix='akuz_writer_custom_') as td:
            root = Path(td)
            raw = root/'custom.log'
            raw.write_text('synthetic', encoding='utf-8')
            def injected(raw, out, base, chunk, top):
                (out/'data').mkdir(parents=True)
                (out/'index.html').write_text('index', encoding='utf-8')
                (out/'data'/'catalog.js').write_text('catalog', encoding='utf-8')
                (out/'data'/'raw_0000.js').write_text('raw', encoding='utf-8')
                return {'events': 1, 'physical_lines': 1,
                        '_output_hashes': {'data/raw_0000.js': ('0'*64, 3)}}
            report = _publish(root, load_store(root), 'injected-key',
                raw, None, [], 'custom', 'single', gen_fn=injected)
            row = load_store(root)['reports'][report['id']]
            self.assertEqual(row['integrity']['all_sha256']['data/raw_0000.js'],
                sha256(root/'reports'/report['id']/'data'/'raw_0000.js'))
            trace = (root/'diagnostics'/'performance.txt').read_text('utf-8')
            self.assertIn('producer_files=0', trace)


if __name__ == '__main__':
    unittest.main()
