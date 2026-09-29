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


class DeltaFakeSSH:
    def __init__(self, data: bytes):
        self.data = data
        self.closed = False
        self.commands = []

    def exec_command(self, command, timeout=120, get_pty=False):
        import re
        self.commands.append(command)
        if "sha256sum" in command:
            match = re.search(r"head -c (\d+) -- ", command)
            if match is None:
                return FakeOutput(b""), FakeOutput(b""), FakeOutput(b"bad sha command")
            size = int(match.group(1))
            digest = hashlib.sha256(self.data[:size]).hexdigest().encode("ascii")
            return FakeOutput(b""), FakeOutput(digest + b"  -\n"), FakeOutput(b"")
        if "tail -c +" in command:
            start = int(re.search(r"tail -c \+(\d+) -- ", command).group(1)) - 1
            length = int(re.search(r"\| head -c (\d+)", command).group(1))
            return (
                FakeOutput(b""),
                FakeOutput(self.data[start:start + length]),
                FakeOutput(b""),
            )
        raise AssertionError("unexpected synthetic SSH command")

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

    def test_delta_resume_fetches_only_append_and_publishes_exact_snapshot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = root / "downloads"
            downloads.mkdir()
            old = b"12:00:00.000,AKUZ,s,user: old\n"
            tail = b"12:01:00.000,AKUZ,s,user: new\n"
            remote_data = old + tail
            previous = downloads / "previous.log"
            previous.write_bytes(old)
            cfg = fetch.ConnectConfig(
                "example.test", 22, "reader", "", "", "",
                "/srv/akuz", downloads, "*.log", "")
            selected = dict(
                id="d" * 64, path="/srv/akuz/test.log", name="test.log",
                size=len(remote_data), device=1, inode=2, mtime=100)
            resume = dict(
                path=str(previous), sha256=hashlib.sha256(old).hexdigest(),
                size=len(old), host=cfg.host, remote=selected["path"],
                snapshot=dict(device=1, inode=2, stored_bytes=len(old)),
                _delta_owner="a" * 12)
            ssh = DeltaFakeSSH(remote_data)
            meta = ((1, 2, len(remote_data), 100), "")
            with patch.object(fetch, "_connect", return_value=ssh), \
                 patch.object(fetch, "_listing", return_value=[selected]), \
                 patch.object(fetch, "_remote_metadata",
                              side_effect=[meta, meta, meta]):
                path, digest, details = fetch.fetch_selected(
                    cfg, selected, trace_root=root, resume=resume)
            self.assertEqual(path.read_bytes(), remote_data)
            self.assertEqual(digest, hashlib.sha256(remote_data).hexdigest())
            self.assertEqual(previous.read_bytes(), old)
            self.assertTrue(details["delta_resume"])
            self.assertEqual(details["previous_bytes"], len(old))
            self.assertEqual(details["stored_bytes"], len(remote_data))
            delta_commands = [x for x in ssh.commands if "tail -c +" in x]
            self.assertEqual(len(delta_commands), 1)
            self.assertIn(f"| head -c {len(tail)}", delta_commands[0])
            self.assertFalse(list(downloads.glob("*.delta-*")))
            self.assertFalse(list(downloads.glob("*.part-*")))
            self.assertTrue(ssh.closed)

    def test_delta_resume_final_sha_mismatch_falls_back_without_publish(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = root / "downloads"
            downloads.mkdir()
            old = b"old-line\n"
            remote_data = old + b"new-line\n"
            previous = downloads / "previous.log"
            previous.write_bytes(old)
            cfg = fetch.ConnectConfig(
                "example.test", 22, "reader", "", "", "",
                "/srv/akuz", downloads, "*.log", "")
            selected = dict(
                id="e" * 64, path="/srv/akuz/test.log", name="test.log",
                size=len(remote_data), device=1, inode=2, mtime=100)
            resume = dict(
                path=str(previous), sha256=hashlib.sha256(old).hexdigest(),
                size=len(old), host=cfg.host, remote=selected["path"],
                snapshot=dict(device=1, inode=2, stored_bytes=len(old)),
                _delta_owner="a" * 12)
            ssh = DeltaFakeSSH(remote_data)
            meta = ((1, 2, len(remote_data), 100), "")
            with patch.object(fetch, "_connect", return_value=ssh), \
                 patch.object(fetch, "_listing", return_value=[selected]), \
                 patch.object(fetch, "_remote_metadata",
                              side_effect=[meta, meta, meta]), \
                 patch.object(fetch, "_remote_prefix_sha256",
                              return_value="0" * 64):
                with self.assertRaises(fetch.DeltaResumeFallback):
                    fetch.fetch_selected(cfg, selected, resume=resume)
            self.assertEqual(previous.read_bytes(), old)
            self.assertEqual(
                [p for p in downloads.iterdir() if p.name != previous.name], [])
            self.assertTrue(ssh.closed)

    def test_delta_resume_active_tail_keeps_only_complete_prefix(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = root / "downloads"
            downloads.mkdir()
            old = b"old-line\n"
            complete = b"complete-line\n"
            unfinished = b"unfinished"
            remote_data = old + complete + unfinished
            previous = downloads / "previous.log"
            previous.write_bytes(old)
            cfg = fetch.ConnectConfig(
                "example.test", 22, "reader", "", "", "",
                "/srv/akuz", downloads, "*.log", "")
            selected = dict(
                id="f" * 64, path="/srv/akuz/test.log", name="test.log",
                size=len(remote_data), device=1, inode=2, mtime=100)
            resume = dict(
                path=str(previous), sha256=hashlib.sha256(old).hexdigest(),
                size=len(old), host=cfg.host, remote=selected["path"],
                snapshot=dict(device=1, inode=2, stored_bytes=len(old)),
                _delta_owner="a" * 12)
            ssh = DeltaFakeSSH(remote_data)
            before = ((1, 2, len(remote_data), 100), "")
            after = ((1, 2, len(remote_data) + 5, 101), "")
            with patch.object(fetch, "_connect", return_value=ssh), \
                 patch.object(fetch, "_listing", return_value=[selected]), \
                 patch.object(fetch, "_remote_metadata",
                              side_effect=[before, after, after]):
                path, digest, details = fetch.fetch_selected(
                    cfg, selected, resume=resume)
            expected = old + complete
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(digest, hashlib.sha256(expected).hexdigest())
            self.assertTrue(details["active"])
            self.assertEqual(details["stored_bytes"], len(expected))
            self.assertEqual(
                details["dropped_tail_bytes"], len(remote_data) - len(expected))

    def test_delta_resume_rejects_legacy_or_external_previous_snapshot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = root / "downloads"
            downloads.mkdir()
            external = root / "outside.log"
            external.write_bytes(b"old\n")
            cfg = fetch.ConnectConfig(
                "example.test", 22, "reader", "", "", "",
                "/srv/akuz", downloads, "*.log", "")
            selected = dict(
                id="1" * 64, path="/srv/akuz/test.log", name="test.log",
                size=10, device=1, inode=2, mtime=100)
            meta = ((1, 2, 10, 100), "")
            for resume in (
                dict(path=str(external), sha256=hashlib.sha256(b"old\n").hexdigest(),
                     size=4, host=cfg.host, remote=selected["path"],
                     snapshot=dict(stored_bytes=4),
                     _delta_owner="a" * 12),
                dict(path=str(external), sha256=hashlib.sha256(b"old\n").hexdigest(),
                     size=4, host=cfg.host, remote=selected["path"],
                     snapshot=dict(device=1, inode=2, stored_bytes=4),
                     _delta_owner="a" * 12),
            ):
                ssh = DeltaFakeSSH(b"old\nmore\n")
                with patch.object(fetch, "_connect", return_value=ssh), \
                     patch.object(fetch, "_listing", return_value=[selected]), \
                     patch.object(fetch, "_remote_metadata", return_value=meta):
                    with self.assertRaises(fetch.DeltaResumeFallback):
                        fetch.fetch_selected(cfg, selected, resume=resume)
                self.assertTrue(ssh.closed)
            self.assertEqual(list(downloads.iterdir()), [])

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

    def test_rotation_during_transfer_rejected_and_part_removed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = b"12:00:00.000,AKUZ,s,user: stable\n"
            cfg = fetch.ConnectConfig("example.test", 22, "reader", "", "",
                "", "/srv/akuz", root/"downloads", "*.log", "")
            selected = dict(id="a"*64, path="/srv/akuz/test.log",
                name="test.log", size=len(data), device=1, inode=2)
            before = ((1, 2, len(data), 100), "")
            after = ((1, 9, len(data), 100), "")
            with patch.object(fetch, "_connect", return_value=FakeSSH(data)), \
                 patch.object(fetch, "_listing", return_value=[selected]), \
                 patch.object(fetch, "_remote_metadata", side_effect=[before, after]):
                with self.assertRaisesRegex(fetch.FetchError, "заменён ротацией"):
                    fetch.fetch_selected(cfg, selected, trace_root=root)
            self.assertEqual(list((root/"downloads").glob("*.part")), [])
            self.assertEqual(list((root/"downloads").glob("akuz_v4_*")), [])

    def test_truncation_during_transfer_rejected_and_part_removed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = b"12:00:00.000,AKUZ,s,user: stable\n"
            cfg = fetch.ConnectConfig("example.test", 22, "reader", "", "",
                "", "/srv/akuz", root/"downloads", "*.log", "")
            selected = dict(id="a"*64, path="/srv/akuz/test.log",
                name="test.log", size=len(data), device=1, inode=2)
            before = ((1, 2, len(data), 100), "")
            after = ((1, 2, len(data)-1, 101), "")
            with patch.object(fetch, "_connect", return_value=FakeSSH(data)), \
                 patch.object(fetch, "_listing", return_value=[selected]), \
                 patch.object(fetch, "_remote_metadata", side_effect=[before, after]):
                with self.assertRaisesRegex(fetch.FetchError, "усечён"):
                    fetch.fetch_selected(cfg, selected, trace_root=root)
            self.assertEqual(list((root/"downloads").glob("*.part")), [])
            self.assertEqual(list((root/"downloads").glob("akuz_v4_*")), [])

    def test_active_without_complete_line_fails_closed_and_removes_part(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = b"12:00:00.000,AKUZ,s,user: unfinished"
            cfg = fetch.ConnectConfig("example.test", 22, "reader", "", "",
                "", "/srv/akuz", root/"downloads", "*.log", "")
            selected = dict(id="a"*64, path="/srv/akuz/test.log",
                name="test.log", size=len(data), device=1, inode=2)
            before = ((1, 2, len(data), 100), "")
            after = ((1, 2, len(data)+1, 101), "")
            with patch.object(fetch, "_connect", return_value=FakeSSH(data)), \
                 patch.object(fetch, "_listing", return_value=[selected]), \
                 patch.object(fetch, "_remote_metadata", side_effect=[before, after]):
                with self.assertRaisesRegex(fetch.FetchError, "нет завершённой строки"):
                    fetch.fetch_selected(cfg, selected, trace_root=root)
            self.assertEqual(list((root/"downloads").glob("*.part")), [])
            self.assertEqual(list((root/"downloads").glob("akuz_v4_*")), [])


if __name__ == "__main__":
    unittest.main()
