"""Collision with orphan publication intents must not abort a new report."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import akuz_app
from akuz_store import load_store

def synthetic_generator(raw, output, base, chunk, top):
    output.mkdir(parents=True)
    (output/'index.html').write_text('synthetic', encoding='utf-8')
    (output/'data').mkdir()
    (output/'data'/'catalog.js').write_text('synthetic', encoding='utf-8')
    (output/'data'/'raw_0000.js').write_text('raw', encoding='utf-8')
    return {'events': 1, 'physical_lines': 1}


STAMP = datetime(2026, 9, 27, 8, 59, 1)
PREFIX = 'v4_20260927_085901_'


class ReportIdCollisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='akuz_phase9_id_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'synthetic.log'
        self.source.write_text('synthetic', encoding='utf-8')
        self.intents = self.root/'cache'/'report_intents'
        self.intents.mkdir(parents=True)

    def publish_with_tokens(self, *tokens):
        with patch('akuz_app.datetime') as clock, patch(
                'akuz_app.secrets.token_hex', side_effect=tokens):
            clock.now.return_value = STAMP
            return akuz_app._publish(self.root, load_store(self.root),
                'synthetic-key', self.source, None, [], 'synthetic',
                'single', gen_fn=synthetic_generator)

    def test_orphan_marker_collision_retries_without_deleting_it(self):
        old = self.intents/(PREFIX+'cafebabe.json')
        old.write_text('preexisting diagnostic evidence', encoding='utf-8')
        result = self.publish_with_tokens('cafebabe', 'feedface')
        self.assertEqual(result['id'], PREFIX+'feedface')
        self.assertFalse(result['reused'])
        self.assertEqual(old.read_text(encoding='utf-8'),
                         'preexisting diagnostic evidence')
        self.assertEqual(len(load_store(self.root)['reports']), 1)

    def test_orphan_draft_collision_retries_without_deleting_it(self):
        old = self.intents/(PREFIX+'cafebabe.json.tmp')
        old.write_text('incomplete previous marker', encoding='utf-8')
        result = self.publish_with_tokens('cafebabe', 'feedface')
        self.assertEqual(result['id'], PREFIX+'feedface')
        self.assertEqual(old.read_text(encoding='utf-8'),
                         'incomplete previous marker')

    def test_ten_reserved_ids_fail_without_touching_evidence(self):
        old = self.intents/(PREFIX+'cafebabe.json')
        old.write_text('leave intact', encoding='utf-8')
        with self.assertRaisesRegex(akuz_app.FetchError,
                                    'Не удалось назначить'):
            self.publish_with_tokens(*(['cafebabe']*10))
        self.assertEqual(old.read_text(encoding='utf-8'), 'leave intact')
        self.assertFalse((self.root/'reports').exists())
        self.assertFalse(load_store(self.root)['reports'])


if __name__ == '__main__':
    unittest.main()
