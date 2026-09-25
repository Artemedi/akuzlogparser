"""Local file/folder source: identity, read-only snapshots, growing logs and HTTP."""
from __future__ import annotations

import hashlib
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import akuz_app as app
from akuz_analytics import refresh
from akuz_fetch import FetchError
from akuz_local import fetch_local, list_local, load_local_config
from akuz_store import clear_cache, load_store


class LocalSourceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'Проверка'
        self.root.mkdir()
        self.logs = self.root / 'source logs'
        self.logs.mkdir()

    def write(self, name='20260923_server.log', message='Failed patient 1234', time='12:00:00.000'):
        path = self.logs / name
        text = f'{time},AKUZ,s1,user: System.InvalidOperationException: {message}\n at AKUZ.Serialize()\n'
        path.write_text(text, encoding='utf-8')
        return path

    def test_local_path_validation_and_nonrecursive_inventory(self):
        first = self.write()
        (self.logs / 'not-a-log.txt').write_text('not a log')
        nested = self.logs / 'nested'
        nested.mkdir()
        (nested / 'secret.log').write_text('not a top-level log')
        cfg = load_local_config(str(self.logs), self.root)
        self.assertEqual(cfg.local_dest, (self.root / 'downloads').resolve())
        self.assertTrue(cfg.host.startswith('local:'))
        found = list_local(cfg)
        self.assertEqual([item['name'] for item in found], [first.name])
        self.assertEqual(list_local(load_local_config(str(first), self.root))[0]['path'], str(first.resolve()))
        for bad in ('', 'relative/path.log', str(self.logs / 'no-such.log'),
                    str(self.logs / 'not-a-log.txt'), '\\\\SERVER\\share\\logs'):
            with self.subTest(bad=bad), self.assertRaises(FetchError):
                load_local_config(bad, self.root)
        link = self.logs / 'alias.log'
        try:
            link.symlink_to(first)
        except OSError:
            pass  # An unprivileged Windows test runner may lack symlink permission.
        else:
            self.assertEqual(len(list_local(cfg)), 1)
            with self.assertRaises(FetchError):
                load_local_config(str(link), self.root)

    def test_cached_snapshots_are_readonly_and_analytics_deduplicates_growth(self):
        original = self.write()
        old_bytes = original.read_bytes()
        state = app.State()
        app.perform_list(self.root, state, source='local', local_path=str(self.logs))
        self.assertEqual(state.local_path, str(self.logs.resolve()))
        old_selections = [{'id': item['id'], 'date': ''} for item in state.listing]
        app.perform_build_current(self.root, state, old_selections)
        old_report = state.result['reports'][0]
        self.assertFalse(state.result['reused'])
        self.assertEqual(refresh(self.root)['distinct_errors'], 1)
        app.perform_build_current(self.root, state, old_selections)
        self.assertTrue(state.result['reused'])
        with original.open('ab') as output:
            output.write(b'13:00:00.000,AKUZ,s1,user: System.InvalidOperationException: Failed patient 5678\n at AKUZ.Serialize()\n')
        app.perform_build_current(self.root, state, old_selections)
        newer = state.result['reports'][0]
        self.assertNotEqual(newer['id'], old_report['id'])
        self.assertEqual(len(load_store(self.root)['reports']), 2)
        self.assertEqual(refresh(self.root)['distinct_errors'], 2)
        self.assertEqual(newer['events'], 2)
        html = (self.root / 'reports' / newer['id'] / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="fetch-source"', html)
        self.assertIn('id="local-path"', html)
        self.assertEqual(original.read_bytes()[:len(old_bytes)], old_bytes)
        self.assertTrue((self.root / 'downloads').is_dir())
        self.assertEqual(newer['sources'][0]['host'], old_report['sources'][0]['host'])

    def test_file_disappears_and_original_is_not_deleted_by_cache_clear(self):
        original = self.write()
        cfg = load_local_config(str(self.logs), self.root)
        item = list_local(cfg)[0]
        with patch('akuz_local._sha_file', side_effect=AssertionError('second disk read')):
            source, digest, details = fetch_local(cfg, item)
        self.assertEqual(source.read_bytes(), original.read_bytes())
        self.assertEqual(digest, hashlib.sha256(original.read_bytes()).hexdigest())
        self.assertFalse(details['active'])
        indexed = {'version':4,'downloads':{'snapshot':{'path':str(source)}},'reports':{}}
        cleared = clear_cache(self.root, cfg, indexed, False)
        self.assertEqual(cleared['downloads_removed'], 1)
        self.assertFalse(source.exists())
        self.assertTrue(original.is_file())
        original.unlink()
        with self.assertRaises(FetchError):
            fetch_local(cfg, item)

    def test_http_list_and_latest_without_ssh_configuration(self):
        original = self.write()
        state = app.State()
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.make_handler(self.root, state, 0))
        # Handler validates Host against the selected port.
        server.RequestHandlerClass = app.make_handler(self.root, state, server.server_port)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(lambda: (server.shutdown(), worker.join(timeout=5), server.server_close()))
        def request(endpoint, payload):
            import json
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                conn.request('POST', endpoint, body=json.dumps(payload),
                    headers={'Origin': f'http://127.0.0.1:{server.server_port}',
                             'Content-Type': 'application/json'})
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()
        def idle():
            until = time.monotonic() + 15
            while time.monotonic() < until:
                with state.lock:
                    if not state.busy:
                        self.assertFalse(state.error, state.error)
                        return
                time.sleep(.03)
            self.fail('Background import did not finish')
        status, error = request('/api/list', {'source':'local'})
        self.assertEqual(status, 400)
        self.assertIn('путь', error['error'])
        status, result = request('/api/list', {'source':'local', 'local_path':str(self.logs)})
        self.assertEqual(status, 202)
        idle()
        self.assertEqual(state.source, 'local')
        self.assertEqual(len(state.listing), 1)
        status, result = request('/api/fetch', {'source':'local', 'local_path':str(original)})
        self.assertEqual(status, 202)
        idle()
        self.assertEqual(state.result['reports'][0]['events'], 1)
        self.assertEqual(state.local_path, str(original.resolve()))
        self.assertEqual(refresh(self.root)['distinct_errors'], 1)


if __name__ == '__main__':
    unittest.main()
