"""Phase 16 stale-browser listing revision gates."""
from __future__ import annotations

import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

import akuz_app as app


class Phase16StaleSessionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="akuz-phase16-stale-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_stale_build_revision_is_rejected_before_worker_admission(self):
        state = app.State()
        with state.lock:
            state.source = "linux"
            state.listing = [{
                "id": "same-id",
                "name": "20260923_server.log",
                "path": "/srv/akuz/20260923_server.log",
            }]
            state.listing_revision = 7

        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), app.make_handler(self.root, state, 0))
        server.RequestHandlerClass = app.make_handler(
            self.root, state, server.server_port)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        conn = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=3)
        try:
            body = json.dumps({
                "selections": [{"id": "same-id", "date": "2026-09-23"}],
                "listing_revision": 6,
            })
            conn.request(
                "POST", "/api/build", body=body,
                headers={
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                    "Content-Type": "application/json",
                })
            response = conn.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 409)
            self.assertIn("другой вкладке", payload["error"])
            with state.lock:
                self.assertFalse(state.busy)
                self.assertEqual(state.listing_revision, 7)
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_listing_revision_type_is_fail_closed(self):
        state = app.State()
        with state.lock:
            state.listing = [{"id": "one", "name": "a.log", "path": "/a.log"}]
            state.listing_revision = 1
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), app.make_handler(self.root, state, 0))
        server.RequestHandlerClass = app.make_handler(
            self.root, state, server.server_port)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        conn = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=3)
        try:
            body = json.dumps({
                "selections": [{"id": "one", "date": ""}],
                "listing_revision": True,
            })
            conn.request(
                "POST", "/api/build", body=body,
                headers={
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                    "Content-Type": "application/json",
                })
            response = conn.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 400)
            self.assertIn("версия списка", payload["error"])
            with state.lock:
                self.assertFalse(state.busy)
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
