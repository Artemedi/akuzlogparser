"""Synthetic SSH snapshots: timing is numeric, snapshot integrity unchanged."""
import hashlib
import io
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import akuz_fetch as fetch


class FakeChannel:
    def shutdown_write(self):
        pass

    def recv_exit_status(self):
        return 0


class FakeOutput(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.channel = FakeChannel()


class FakeSSH:
    def __init__(self, data):
        self.data = data
        self.closed = False
    def exec_command(self, command, timeout=120, get_pty=False):
        self.command = command
        return FakeOutput(b""), FakeOutput(self.data), FakeOutput(b"")

    def close(self):
        self.closed = True


class SSHTraceTests(unittest.TestCase):
    def snapshot(self, data, active=False):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = fetch.ConnectConfig("example.test", 22, "reader", "", "",
                "", "/srv/akuz", root/"downloads", "*.log", "")
            selected = dict(id="a"*64, path="/srv/akuz/test.log",
                name="test.log", size=len(data), device=1, inode=2)
            before = ((1,2,len(data),100),"")
            after = ((1,2,len(data)+1,101),"") if active else before
            ssh = FakeSSH(data)
            with patch.object(fetch, "_connect", return_value=ssh), \
                 patch.object(fetch, "_listing", return_value=[selected]), \
                 patch.object(fetch, "_remote_metadata", side_effect=[before,after]):
                path, digest, details = fetch.fetch_selected(
                    cfg, selected, trace_root=root)
            expected = data[:data.rfind(b"\n")+1] if active else data
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(digest, hashlib.sha256(expected).hexdigest())
            self.assertEqual(details["active"], active)
            self.assertTrue(ssh.closed)
            trace = (root/"diagnostics"/"performance.txt").read_text("utf-8")
            for stage in ("connect","inventory","stat_before",
                          "transfer","stat_after"):
                self.assertIn("stage=source.ssh."+stage+" status=done", trace)
            self.assertNotIn("/srv/akuz/test.log", trace)
            self.assertNotIn("example.test", trace)
            self.assertIn("stage=source.ssh.transfer status=summary", trace)
            self.assertIn("stage=source.ssh.connect status=start compression=0", trace)
            self.assertIn("stage=source.ssh.transfer status=start bytes_expected=", trace)
            self.assertIn("compression=0", trace)
            self.assertIn("mib_per_s=", trace)
            return trace

    def test_ssh_compression_is_opt_in(self):
        class Client:
            def load_system_host_keys(self):
                pass
            def load_host_keys(self, path):
                pass
            def set_missing_host_key_policy(self, policy):
                pass
            def connect(self, **kwargs):
                self.kwargs = kwargs
            def get_transport(self):
                return None
            def close(self):
                pass
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root/"ConnectConf.cfg"
            common = ("[ssh]\nhost = example.test\nusername = reader\n"
                      "[logs]\nremote_log_dir = /srv/akuz\n")
            with patch.dict(sys.modules, {"paramiko": types.SimpleNamespace(
                    RejectPolicy=object)}):
                for configured, expected in ((common, False),
                                             (common.replace("[logs]",
                                              "compression = true\n[logs]"), True)):
                    config.write_text(configured, encoding="utf-8")
                    cfg = fetch.load_config(config, root)
                    client = fetch._connect(cfg, client_factory=Client)
                    self.assertEqual(client.kwargs["compress"], expected)
                    client.close()

    def test_static(self):
        trace = self.snapshot(b"12:00:00.000,AKUZ,s,user: synthetic\n")
        self.assertNotIn("source.ssh.trim_rehash", trace)

    def test_active_incomplete_tail(self):
        trace = self.snapshot(b"12:00:00.000,AKUZ,s,user: complete\n"
                              b"12:00:01.000,AKUZ,s,user: incomplete",active=True)
        self.assertIn("stage=source.ssh.trim_rehash status=done", trace)

    def test_short_transfer_rejected_and_part_removed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = fetch.ConnectConfig("example.test", 22, "reader", "", "",
                "", "/srv/akuz", root/"downloads", "*.log", "")
            selected = dict(id="a"*64, path="/srv/akuz/test.log",
                name="test.log", size=100, device=1, inode=2)
            with patch.object(fetch, "_connect", return_value=FakeSSH(b"too short")), \
                 patch.object(fetch, "_listing", return_value=[selected]), \
                 patch.object(fetch, "_remote_metadata",
                              return_value=((1,2,100,100),"")):
                with self.assertRaisesRegex(fetch.FetchError, "Передача неполная"):
                    fetch.fetch_selected(cfg, selected, trace_root=root)
            self.assertEqual(list((root/"downloads").glob("*.part")), [])
            self.assertIn("stage=source.ssh.transfer status=done",
                          (root/"diagnostics"/"performance.txt").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
