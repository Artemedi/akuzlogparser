"""Post-commit readback failures must not orphan an indexed report."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import akuz_app
import akuz_store
from akuz_store import load_store, save_store


class InventoryReadbackTests(unittest.TestCase):
    @staticmethod
    def generator(raw, output, base, chunk, top):
        output.mkdir(parents=True)
        (output/'index.html').write_text('synthetic', encoding='utf-8')
        (output/'data').mkdir()
        (output/'data'/'catalog.js').write_text('synthetic', encoding='utf-8')
        return {'events': 1, 'physical_lines': 1}

    def test_no_inventory_readback_after_published_replace(self):
        with TemporaryDirectory(prefix='akuz_inventory_readback_') as td:
            root = Path(td)
            raw = root/'synthetic.log'
            raw.write_text('synthetic', encoding='utf-8')
            baseline = load_store(root)
            baseline['downloads']['old'] = {'marker': 'old'}
            save_store(root, baseline)
            original = akuz_store._disk_revision
            calls = []
            def fail_after_replace(path):
                calls.append(path.name)
                if len(calls) > 1:
                    raise OSError('post-commit readback blocked')
                return original(path)
            with patch('akuz_store._disk_revision', side_effect=fail_after_replace):
                result = akuz_app._publish(root, load_store(root),
                    'unique-key', raw, None, [], 'synthetic', 'single',
                    gen_fn=self.generator)
            self.assertFalse(result['reused'])
            self.assertEqual(calls, ['inventory.json'])
            saved = load_store(root)
            self.assertIn(result['id'], saved['reports'])
            self.assertTrue((root/'reports'/result['id']/'index.html').is_file())
            self.assertEqual(saved['downloads']['old'], {'marker': 'old'})

    def test_post_replace_inventory_path_is_never_read(self):
        with TemporaryDirectory(prefix='akuz_readback_boundary_') as td:
            root = Path(td)
            raw = root/'synthetic.log'
            raw.write_text('synthetic', encoding='utf-8')
            save_store(root, load_store(root))
            original_replace = Path.replace
            original_read = Path.read_bytes
            state = {'published': False, 'post_reads': 0}
            def mark_replace(path, target):
                result = original_replace(path, target)
                if path.name == 'inventory.json.tmp':
                    state['published'] = True
                return result
            def prohibit_post_read(path):
                if state['published'] and path.name == 'inventory.json':
                    state['post_reads'] += 1
                    raise OSError('forbidden inventory read after replace')
                return original_read(path)
            with patch.object(Path, 'replace', mark_replace), patch.object(
                    Path, 'read_bytes', prohibit_post_read):
                report = akuz_app._publish(root, load_store(root),
                    'readback-boundary', raw, None, [], 'synthetic',
                    'single', gen_fn=self.generator)
            self.assertTrue(state['published'])
            self.assertEqual(state['post_reads'], 0)
            self.assertIn(report['id'], load_store(root)['reports'])
            self.assertTrue((root/'reports'/report['id']/'index.html').is_file())

    def test_temp_digest_read_error_prevents_commit_and_cleans_tmp(self):
        with TemporaryDirectory(prefix='akuz_inventory_digest_') as td:
            root = Path(td)
            entry = load_store(root)
            entry['downloads']['old'] = {'marker': 'old'}
            save_store(root, entry)
            inventory = root/'cache'/'inventory.json'
            before = inventory.read_bytes()
            value = load_store(root)
            value['downloads']['new'] = {'marker': 'new'}
            original = Path.read_bytes
            def fail_temp(path):
                if path.name == 'inventory.json.tmp':
                    raise OSError('synthetic temp digest read failure')
                return original(path)
            with patch.object(Path, 'read_bytes', fail_temp):
                with self.assertRaisesRegex(OSError, 'temp digest'):
                    save_store(root, value)
            self.assertEqual(inventory.read_bytes(), before)
            self.assertFalse(inventory.with_name('inventory.json.tmp').exists())
            self.assertEqual(set(load_store(root)['downloads']), {'old'})

if __name__ == '__main__':
    unittest.main()
