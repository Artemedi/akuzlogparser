"""Read-only Windows SMB adapter and v4.1 source workflow tests (no real SMB required)."""
import hashlib
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import akuz_app as app
import akuz_windows as win
from akuz_fetch import FetchError
from akuz_store import clear_cache


class WindowsSourceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.share = self.root / 'share'
        self.share.mkdir()
        self.downloads = self.root / 'downloads'
        self.cfg = win.WindowsConfig('test-host', 445, 'tester', '', '',
                                     str(self.share), self.downloads, '*.log')
        self.fake_os = types.SimpleNamespace(name='nt', fstat=os.fstat)

    def write_log(self, name='akuz.log', data=b'12:00:00.000,AKUZ,s1,user: OK\n'):
        path = self.share / name
        path.write_bytes(data)
        return path

    def test_unc_config(self):
        config = self.root / 'ConnectConf.cfg'
        config.write_text(
            '[windows]\nenabled = true\n'
            r'log_dir = \\SERVER\AKUZLogs'+'\nfile_pattern = *.log\n', encoding='utf-8')
        loaded = win.load_windows_config(config, self.root)
        self.assertEqual(loaded.host, 'server')
        self.assertEqual(loaded.remote_log_dir, r'\\SERVER\AKUZLogs')
        config.write_text('[windows]\nenabled = true\nlog_dir = /tmp/notunc\n')
        with self.assertRaises(FetchError):
            win.load_windows_config(config, self.root)

    def test_growth_new_snapshot_id(self):
        p = self.write_log()
        first = win._inventory(self.share, self.cfg)[0]
        with p.open('ab') as f:
            f.write(b'12:00:01.000,AKUZ,s1,user: Next\n')
        second = win._inventory(self.share, self.cfg)[0]
        self.assertNotEqual(first['id'], second['id'])
        self.assertGreater(second['size'], first['size'])

    def test_static_without_newline_preserved(self):
        data = b'12:00:00.000,AKUZ,s1,user: final'
        self.write_log(data=data)
        selected = win._inventory(self.share, self.cfg)[0]
        with patch.object(win, 'os', self.fake_os):
            path, digest, meta = win.fetch_windows(self.cfg, selected)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(digest, hashlib.sha256(data).hexdigest())
        self.assertFalse(meta['active'])

    def test_live_growth_trims_incomplete_tail(self):
        initial = b'12:00:00.000,AKUZ,s1,user: complete\n12:00:01.000,AKUZ,s1,user: incom'
        p = self.write_log(data=initial)
        selected = win._inventory(self.share, self.cfg)[0]
        calls = 0
        def growing_fstat(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                with p.open('ab') as f:
                    f.write(b'plete\n')
            return os.fstat(fd)
        with patch.object(win, 'os', types.SimpleNamespace(name='nt', fstat=growing_fstat)):
            path, digest, meta = win.fetch_windows(self.cfg, selected)
        expected = initial.split(b'\n')[0]+b'\n'
        self.assertEqual(path.read_bytes(), expected)
        self.assertTrue(meta['active'])
        self.assertEqual(meta['dropped_tail_bytes'], len(initial)-len(expected))
        self.assertEqual(digest, hashlib.sha256(expected).hexdigest())
        self.assertEqual(p.read_bytes(), initial+b'plete\n')

    def test_rotation_refused(self):
        p = self.write_log()
        selected = win._inventory(self.share, self.cfg)[0]
        p.unlink()
        self.write_log(data=b'12:00:00.000,AKUZ,s1,user: replacement\n')
        with patch.object(win, 'os', self.fake_os), self.assertRaises(FetchError):
            win.fetch_windows(self.cfg, selected)

    def test_separate_and_combined_reports_and_reuse(self):
        self.write_log('first.log')
        self.write_log('second.log', b'13:00:00.000,AKUZ,s2,user: Other\n')
        state = app.State()
        def listing(cfg, source, notify):
            self.assertEqual(source, 'windows')
            return win._inventory(self.share, cfg)
        def fetch(cfg, source, remote, notify):
            self.assertEqual(source, 'windows')
            data = Path(remote['path']).read_bytes()
            self.downloads.mkdir(exist_ok=True)
            target = self.downloads / ('akuz_v4_win_'+remote['id'][:18]+'_'+remote['name'])
            target.write_bytes(data)
            return target, hashlib.sha256(data).hexdigest(), {'active': False}
        def generate(raw, folder, base, chunk, toplimit):
            folder.mkdir(parents=True)
            (folder/'index.html').write_text('HTML report', encoding='utf-8')
            (folder/'data').mkdir()
            (folder/'data'/'catalog.js').write_text('window.AKUZ_DATA={}', encoding='utf-8')
            return {'events': 1, 'physical_lines': 1}
        with patch.object(app, 'source_config', return_value=self.cfg), \
             patch.object(app, 'source_list', side_effect=listing), \
             patch.object(app, 'source_fetch', side_effect=fetch):
            app.perform_list(self.root, state, source='windows')
            self.assertEqual(state.source, 'windows')
            selected = [{'id': f['id'], 'date': '2026-09-22'} for f in state.listing]
            app.perform_build(self.root, state, selected, gen_fn=generate, refresh_remote=True)
            self.assertEqual(len(state.result['reports']), 2)
            self.assertIsNotNone(state.result['combined'])
            app.perform_build(self.root, state, selected, gen_fn=generate, refresh_remote=True)
            self.assertTrue(state.result['reused'])
            self.assertEqual(len(list((self.root/'reports').iterdir())), 3)

    def test_same_bytes_different_paths_are_separate_reports(self):
        from unittest.mock import patch
        from akuz_analytics import refresh
        sample=(b"12:00:00.000,AKUZ,s1,user: SerializationException: "
                b"Failed patient 1234\n at AKUZ.Serialize()\n")
        self.write_log("one.log",sample)
        self.write_log("two.log",sample)
        state=app.State()
        def fetch(cfg,source,remote,notify):
            original=Path(remote["path"])
            self.downloads.mkdir(exist_ok=True)
            target=self.downloads/("akuz_v4_win_"+remote["id"][:18]+"_"+remote["name"])
            data=original.read_bytes()
            target.write_bytes(data)
            return target, hashlib.sha256(data).hexdigest(),{"active":False}
        with patch.object(app,"source_config",return_value=self.cfg), \
             patch.object(app,"source_list",
                 side_effect=lambda cfg,source,notify:win._inventory(self.share,cfg)), \
             patch.object(app,"source_fetch",side_effect=fetch):
            app.perform_list(self.root,state,source="windows")
            choices=[{"id":item["id"],"date":"2026-09-22"} for item in state.listing]
            app.perform_build(self.root,state,choices,refresh_remote=True)
        singles=[r for r in state.result["reports"] if r["kind"]=="single"]
        self.assertEqual(len(singles),2)
        self.assertEqual(len({r["id"] for r in singles}),2)
        self.assertIsNotNone(state.result["combined"])
        self.assertEqual(refresh(self.root)["distinct_errors"],2)

    def test_cache_clear_only_tracked(self):
        self.downloads.mkdir()
        tracked = self.downloads/'akuz_v4_win_tracked.log'
        tracked.write_bytes(b'a')
        other = self.downloads/'do_not_delete.log'
        other.write_bytes(b'b')
        store = {'version': 4,
                 'downloads': {'one': {'path': str(tracked)}},
                 'reports': {}}
        result = clear_cache(self.root, [self.cfg], store, False)
        self.assertEqual(result['downloads_removed'], 1)
        self.assertFalse(tracked.exists())
        self.assertTrue(other.exists())

    def test_windows_trace_and_linux_events_remain_independent(self):
        from collections import Counter
        from akuz_log_parser import event_stream
        source = self.root/'trace.log'
        source.write_text(
            "user 'ina' 11:06:00.123: Windows message\n"
            "continuation line\n"
            "Database: user 'operator' 11:06:01.100: SQL message\n"
            "11:06:02.000,AKUZ,session-1,user: Linux message\n", encoding='utf-8')
        stats = Counter()
        items = list(event_stream(source, stats))
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]['user'], 'ina')
        self.assertEqual(items[0]['component'], 'Windows Trace')
        self.assertEqual(items[0]['request_id'], '')
        self.assertIn('continuation line', items[0]['raw'])
        self.assertEqual(items[1]['component'], 'Database')
        self.assertEqual(items[2]['request_id'], 'session-1')
        self.assertEqual(items[2]['component'], 'AKUZ')
        self.assertEqual(stats['windows_trace_events'], 2)
        self.assertEqual(stats['physical_lines'], 4)

    def test_bogus_windows_time_kept_as_continuation(self):
        from collections import Counter
        from akuz_log_parser import event_stream
        source = self.root/'trace.log'
        source.write_text("user 'ina' 29:59:02.333: wrong\n", encoding='utf-8')
        items=list(event_stream(source, Counter()))
        self.assertEqual(items[0]['component'], '(до первой записи)')


class LocalApiTests(unittest.TestCase):
    def test_empty_catalog_and_config_not_public(self):
        import http.client
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch)
            (root/'ConnectConf.cfg').write_text('password = SECRET')
            server=ThreadingHTTPServer(('127.0.0.1',0),BaseHTTPRequestHandler)
            server.RequestHandlerClass=app.make_handler(root,app.State(),server.server_port)
            worker=threading.Thread(target=server.serve_forever,daemon=True)
            worker.start()
            try:
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                try:
                    conn.request('GET','/data/catalog.js')
                    res=conn.getresponse()
                    data=res.read().decode('utf-8')
                    self.assertEqual(res.status,200)
                    self.assertIn('window.AKUZ_DATA=',data)
                    self.assertIn('"events":0',data)
                    conn.request('GET','/ConnectConf.cfg')
                    res=conn.getresponse()
                    self.assertEqual(res.status,404)
                    self.assertNotIn('SECRET',res.read().decode('utf-8'))
                    conn.request('POST','/api/list',
                                 body='{"source":"other"}',
                                 headers={'Origin':'http://127.0.0.1:'+str(server.server_port),
                                          'Content-Type':'application/json'})
                    res=conn.getresponse()
                    self.assertEqual(res.status,400)
                    self.assertIn('Неизвестный источник',res.read().decode('utf-8'))
                finally:
                    conn.close()
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
