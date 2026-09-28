"""Synthetic contract tests for replicated Phase 10 SSH compression A/B."""
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import bench_phase10_ssh_compression_ab as bench


class RawSocket:
    def __init__(self):
        self.incoming = io.BytesIO(b"abcdef")
        self.sent = bytearray()
    def recv(self, size, *args, **kwargs):
        return self.incoming.read(size)
    def recv_into(self, buffer, *args, **kwargs):
        data = self.incoming.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)
    def send(self, data, *args, **kwargs):
        self.sent.extend(data)
        return len(data)
    def sendall(self, data, *args, **kwargs):
        self.sent.extend(data)
    def close(self):
        pass


class Phase10CompressionABTests(unittest.TestCase):
    def test_counting_socket_counts_recv_recv_into_send_and_sendall(self):
        raw = RawSocket()
        sock = bench.CountingSocket(raw)
        self.assertEqual(sock.recv(2), b"ab")
        target = bytearray(3)
        self.assertEqual(sock.recv_into(target), 3)
        self.assertEqual(bytes(target), b"cde")
        self.assertEqual(sock.send(b"xy"), 2)
        sock.sendall(b"z12")
        self.assertEqual(sock.rx_bytes, 5)
        self.assertEqual(sock.tx_bytes, 5)
        self.assertEqual(bytes(raw.sent), b"xyz12")

    def test_parse_server_cpu(self):
        seconds, ticks = bench._parse_server_cpu(
            b"noise\nAKUZ_PHASE10_SSHD_CPU 100 127 100\n")
        self.assertEqual(ticks, 27)
        self.assertAlmostEqual(seconds, .27)
        for bad in (
            b"",
            b"AKUZ_PHASE10_SSHD_CPU -1 3 100",
            b"AKUZ_PHASE10_SSHD_CPU 9 3 100",
            b"AKUZ_PHASE10_SSHD_CPU 1 2 0",
        ):
            with self.subTest(bad=bad), self.assertRaises(AssertionError):
                bench._parse_server_cpu(bad)

    def test_summary_requires_three_trials(self):
        rows = [
            dict(mode="control", wall_s=v, client_cpu_s=v/2,
                 server_sshd_cpu_s=v/4, socket_rx_bytes=int(v*100),
                 socket_rx_ratio=v/10)
            for v in (3.0, 1.0, 2.0)
        ]
        result = bench._summary(rows, "control")
        self.assertEqual(result["wall_median_s"], 2.0)
        self.assertEqual(result["socket_rx_median_bytes"], 200)
        with self.assertRaisesRegex(AssertionError, "three trials"):
            bench._summary(rows[:2], "control")

    def test_run_balanced_order_and_same_sha(self):
        selected = dict(name="20260923_server.log", path="/srv/a.log",
                        size=1000, device=1, inode=2)
        cfg = SimpleNamespace()
        calls = []
        def trial(cfg, item, bound, compression):
            calls.append(compression)
            n = len(calls)
            return dict(
                mode="compressed" if compression else "control",
                logical_bytes=bound, snapshot_sha256="a"*64,
                wall_s=float(n), client_cpu_s=float(n)/10,
                socket_rx_bytes=500 if compression else 1100,
                socket_tx_bytes=50, socket_rx_ratio=.5 if compression else 1.1,
                server_sshd_cpu_s=.2 if compression else .1,
                server_sshd_cpu_ticks=20 if compression else 10,
                size_before=1000, size_after=1000)
        with patch.object(bench, "load_config", return_value=cfg), \
             patch("akuz_fetch.list_remote", return_value=[selected]), \
             patch.object(bench, "_trial", side_effect=trial):
            result = bench.run(Path("cfg"), Path("root"), "20260923")
        self.assertEqual(calls, [False, True, True, False, False, True])
        self.assertEqual(result["fixed_prefix_bytes"], 1000)
        self.assertEqual(result["summary"]["control"]["trials"], 3)
        self.assertEqual(result["summary"]["compressed"]["trials"], 3)
        self.assertFalse(result["raw_payload_saved"])
        self.assertFalse(result["server_page_cache_controlled"])
        self.assertFalse(result["cold_cache_claim"])

    def test_run_rejects_prefix_digest_change(self):
        selected = dict(name="20260923_server.log", path="/srv/a.log",
                        size=1000, device=1, inode=2)
        cfg = SimpleNamespace()
        count = 0
        def trial(cfg, item, bound, compression):
            nonlocal count
            count += 1
            return dict(
                mode="compressed" if compression else "control",
                logical_bytes=bound,
                snapshot_sha256=("a"*64 if count == 1 else "b"*64),
                wall_s=1.0, client_cpu_s=.1,
                socket_rx_bytes=1000, socket_tx_bytes=1,
                socket_rx_ratio=1.0, server_sshd_cpu_s=.1,
                server_sshd_cpu_ticks=10,
                size_before=1000, size_after=1000)
        with patch.object(bench, "load_config", return_value=cfg), \
             patch("akuz_fetch.list_remote", return_value=[selected]), \
             patch.object(bench, "_trial", side_effect=trial):
            with self.assertRaisesRegex(AssertionError, "SHA changed"):
                bench.run(Path("cfg"), Path("root"), "20260923")


if __name__ == "__main__":
    unittest.main()
