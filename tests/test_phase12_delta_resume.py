"""Phase 12 delta/resume contract probe; no production runtime integration.

This suite models the minimum correctness contract before fetch_selected() may
reuse an older SSH snapshot. All data is synthetic. The deliberately strict
prototype requires a cryptographic proof of the old remote prefix and of the
new published prefix; performance/segment sampling is a later gate.
"""
from __future__ import annotations

import errno
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import akuz_delta
from akuz_delta import (
    RemoteMeta, ResumeRejected, assemble_delta_contract,
    assemble_delta_final_proof,
)
from scripts import bench_phase12_delta_resume_smoke as delta_smoke
from scripts import bench_phase12_delta_resume_ab as delta_ab
from scripts import bench_phase12_delta_resume_v2_ab as delta_v2_ab


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Phase12DeltaResumeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="akuz_phase12_delta_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.previous = self.root / "previous.log"
        self.delta = self.root / "delta.bin"
        self.final = self.root / "next.log"
        self.device = 77
        self.inode = 101

    def run_contract(
        self,
        old: bytes,
        remote: bytes,
        *,
        publish_size: int | None = None,
        before: RemoteMeta | None = None,
        after: RemoteMeta | None = None,
        remote_old: bytes | None = None,
        remote_published: bytes | None = None,
        delta_bytes: bytes | None = None,
        fail_after_written: int | None = None,
    ):
        self.previous.write_bytes(old)
        publish_size = len(remote) if publish_size is None else publish_size
        before = before or RemoteMeta(self.device, self.inode, len(remote), 200)
        after = after or RemoteMeta(self.device, self.inode, len(remote), 200)
        remote_old = old if remote_old is None else remote_old
        remote_published = (
            remote[:publish_size] if remote_published is None else remote_published
        )
        delta_bytes = remote[len(old):] if delta_bytes is None else delta_bytes
        self.delta.write_bytes(delta_bytes)
        return assemble_delta_contract(
            self.previous,
            previous_sha256=digest_bytes(old),
            previous_device=self.device,
            previous_inode=self.inode,
            before=before,
            remote_previous_prefix_sha256=digest_bytes(remote_old),
            delta=self.delta,
            transfer_bound=len(remote),
            publish_size=publish_size,
            after=after,
            remote_published_prefix_sha256=digest_bytes(remote_published),
            final=self.final,
            fail_after_written=fail_after_written,
        )

    def run_contract_v2(
        self,
        old: bytes,
        remote: bytes,
        *,
        publish_size: int | None = None,
        after: RemoteMeta | None = None,
        remote_published: bytes | None = None,
        delta_bytes: bytes | None = None,
        fail_after_written: int | None = None,
    ):
        self.previous.write_bytes(old)
        publish_size = len(remote) if publish_size is None else publish_size
        before = RemoteMeta(self.device, self.inode, len(remote), 200)
        after = after or RemoteMeta(self.device, self.inode, len(remote), 200)
        remote_published = (
            remote[:publish_size] if remote_published is None else remote_published
        )
        delta_bytes = remote[len(old):] if delta_bytes is None else delta_bytes
        self.delta.write_bytes(delta_bytes)
        return assemble_delta_final_proof(
            self.previous,
            previous_sha256=digest_bytes(old),
            previous_device=self.device,
            previous_inode=self.inode,
            before=before,
            delta=self.delta,
            transfer_bound=len(remote),
            publish_size=publish_size,
            after=after,
            remote_published_prefix_sha256=digest_bytes(remote_published),
            final=self.final,
            fail_after_written=fail_after_written,
        )

    def assert_failure_preserves_previous(self, old: bytes):
        self.assertEqual(self.previous.read_bytes(), old)
        self.assertFalse(self.final.exists())
        self.assertFalse(self.final.with_name(self.final.name + ".part").exists())

    def test_append_publishes_byte_exact_full_snapshot(self):
        old = b"event-1\nevent-2\n"
        remote = old + b"event-3\nevent-4\n"
        size, sha = self.run_contract(old, remote)
        self.assertEqual(size, len(remote))
        self.assertEqual(sha, digest_bytes(remote))
        self.assertEqual(self.final.read_bytes(), remote)
        self.assertEqual(self.previous.read_bytes(), old)

    def test_rotation_before_transfer_fails_closed(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        with self.assertRaisesRegex(ResumeRejected, "identity changed before"):
            self.run_contract(
                old,
                remote,
                before=RemoteMeta(self.device, self.inode + 1, len(remote), 200),
            )
        self.assert_failure_preserves_previous(old)

    def test_truncation_before_transfer_fails_closed(self):
        old = b"event-1\nevent-2\n"
        remote = b"event-1\n"
        self.previous.write_bytes(old)
        self.delta.write_bytes(b"")
        with self.assertRaisesRegex(ResumeRejected, "truncated"):
            assemble_delta_contract(
                self.previous,
                previous_sha256=digest_bytes(old),
                previous_device=self.device,
                previous_inode=self.inode,
                before=RemoteMeta(self.device, self.inode, len(remote), 200),
                remote_previous_prefix_sha256=digest_bytes(old),
                delta=self.delta,
                transfer_bound=len(remote),
                publish_size=len(remote),
                after=RemoteMeta(self.device, self.inode, len(remote), 200),
                remote_published_prefix_sha256=digest_bytes(remote),
                final=self.final,
            )
        self.assert_failure_preserves_previous(old)

    def test_same_inode_in_place_prefix_change_is_detected_cryptographically(self):
        old = b"event-A\nevent-B\n"
        changed_prefix = b"event-X\nevent-B\n"
        remote = changed_prefix + b"event-C\n"
        with self.assertRaisesRegex(ResumeRejected, "saved prefix no longer matches"):
            self.run_contract(old, remote, remote_old=changed_prefix)
        self.assert_failure_preserves_previous(old)

    def test_short_network_transfer_is_never_published(self):
        old = b"event-1\n"
        remote = old + b"event-2\nevent-3\n"
        short_delta = remote[len(old):-3]
        with self.assertRaisesRegex(ResumeRejected, "delta length"):
            self.run_contract(old, remote, delta_bytes=short_delta)
        self.assert_failure_preserves_previous(old)

    def test_disk_full_during_assembly_cleans_part_and_keeps_old_snapshot(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        with self.assertRaises(OSError) as caught:
            self.run_contract(old, remote, fail_after_written=len(old) + 2)
        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        self.assert_failure_preserves_previous(old)

    def test_mutation_during_transfer_is_detected_by_final_prefix_proof(self):
        old = b"event-1\n"
        fetched = old + b"event-2\n"
        actual_after = old + b"event-X\n"
        with self.assertRaisesRegex(ResumeRejected, "new remote prefix proof"):
            self.run_contract(
                old,
                fetched,
                remote_published=actual_after,
                after=RemoteMeta(self.device, self.inode, len(fetched), 201),
            )
        self.assert_failure_preserves_previous(old)

    def test_rotation_after_transfer_is_not_accepted_even_when_bytes_match(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        with self.assertRaisesRegex(ResumeRejected, "identity changed during"):
            self.run_contract(
                old,
                remote,
                after=RemoteMeta(self.device, self.inode + 99, len(remote), 201),
            )
        self.assert_failure_preserves_previous(old)

    def test_resume_offset_is_stored_bytes_not_old_capture_bound(self):
        # Prior active capture ended inside UTF-8 and an unfinished physical
        # line. Existing fetch semantics keep only the previous newline.
        old_stored = b"12:00 old event\n"
        old_transfer_bound = old_stored + b"partial " + b"\xd0"
        remote = old_stored + "partial ж\n13:00 next\n".encode("utf-8")
        self.assertGreater(len(old_transfer_bound), len(old_stored))
        size, sha = self.run_contract(old_stored, remote)
        self.assertEqual(size, len(remote))
        self.assertEqual(sha, digest_bytes(remote))
        self.assertEqual(self.final.read_bytes(), remote)
        self.assertIn("ж".encode("utf-8"), self.final.read_bytes())

    def test_active_unfinished_tail_publishes_only_complete_remote_prefix(self):
        old = b"12:00 old event\n"
        remote = old + b"13:00 complete\n14:00 unfinished"
        publish_size = remote.rfind(b"\n") + 1
        size, sha = self.run_contract(old, remote, publish_size=publish_size)
        expected = remote[:publish_size]
        self.assertEqual(size, len(expected))
        self.assertEqual(sha, digest_bytes(expected))
        self.assertEqual(self.final.read_bytes(), expected)
        self.assertFalse(self.final.read_bytes().endswith(b"unfinished"))

    def test_replayed_delta_from_another_version_is_rejected(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        replay = b"event-X\n"
        self.assertEqual(len(replay), len(remote) - len(old))
        with self.assertRaisesRegex(ResumeRejected, "new remote prefix proof"):
            self.run_contract(old, remote, delta_bytes=replay)
        self.assert_failure_preserves_previous(old)

    def test_v2_single_final_proof_publishes_byte_exact_snapshot(self):
        old = b"event-1\nevent-2\n"
        remote = old + b"event-3\nevent-4\n"
        with patch.object(
                akuz_delta, "digest_file",
                side_effect=AssertionError("v2 must not reread full files")):
            size, sha = self.run_contract_v2(old, remote)
        self.assertEqual(size, len(remote))
        self.assertEqual(sha, digest_bytes(remote))
        self.assertEqual(self.final.read_bytes(), remote)
        self.assertEqual(self.previous.read_bytes(), old)

    def test_v2_final_proof_detects_same_inode_old_prefix_rewrite(self):
        old = b"event-A\nevent-B\n"
        remote_downloaded = old + b"event-C\n"
        remote_actual = b"event-X\nevent-B\nevent-C\n"
        self.assertEqual(len(remote_downloaded), len(remote_actual))
        with self.assertRaisesRegex(ResumeRejected, "new remote prefix proof"):
            self.run_contract_v2(
                old, remote_downloaded, remote_published=remote_actual)
        self.assert_failure_preserves_previous(old)

    def test_v2_active_tail_writes_only_verified_complete_prefix(self):
        old = b"12:00 old\n"
        remote = old + b"13:00 complete\n14:00 unfinished"
        publish_size = remote.rfind(b"\n") + 1
        size, sha = self.run_contract_v2(
            old, remote, publish_size=publish_size)
        expected = remote[:publish_size]
        self.assertEqual(size, len(expected))
        self.assertEqual(sha, digest_bytes(expected))
        self.assertEqual(self.final.read_bytes(), expected)
        self.assertFalse(self.final.with_name(self.final.name + ".part").exists())

    def test_v2_disk_full_cleans_owned_part(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        with self.assertRaises(OSError) as caught:
            self.run_contract_v2(
                old, remote, fail_after_written=len(old) + 2)
        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        self.assert_failure_preserves_previous(old)

    def test_existing_target_is_never_overwritten(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        self.final.write_bytes(b"operator-owned")
        with self.assertRaisesRegex(ResumeRejected, "target already exists"):
            self.run_contract(old, remote)
        self.assertEqual(self.final.read_bytes(), b"operator-owned")
        self.assertEqual(self.previous.read_bytes(), old)

    def test_publish_race_never_overwrites_new_final(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        real_link = akuz_delta.os.link

        def racing_link(source, target):
            Path(target).write_bytes(b"other-writer")
            return real_link(source, target)

        with patch.object(akuz_delta.os, "link", side_effect=racing_link):
            with self.assertRaises(FileExistsError):
                self.run_contract(old, remote)
        self.assertEqual(self.final.read_bytes(), b"other-writer")
        self.assertEqual(self.previous.read_bytes(), old)
        self.assertFalse(self.final.with_name(self.final.name + ".part").exists())

    def test_smoke_public_metrics_redact_sha_and_source_identity(self):
        row = dict(
            mode="strict_delta",
            bound_bytes=100,
            previous_bytes=80,
            logical_transfer_bytes=20,
            wall_s=1.25,
            client_cpu_s=0.5,
            old_prefix_sha_wall_s=0.2,
            transfer_wall_s=0.3,
            new_prefix_sha_wall_s=0.4,
            assemble_wall_s=0.35,
            socket_rx_bytes=123,
            socket_tx_bytes=45,
            snapshot_sha256="secret-digest",
            delta_sha256="secret-delta",
            host="secret-host",
            remote_path="/secret/path",
            inode=777,
        )
        public = delta_smoke._public_row(row)
        self.assertEqual(public["mode"], "strict_delta")
        self.assertEqual(public["logical_transfer_bytes"], 20)
        for forbidden in (
                "snapshot_sha256", "delta_sha256", "host",
                "remote_path", "inode"):
            self.assertNotIn(forbidden, public)

    def test_replicated_ab_public_summary_redacts_trial_digests(self):
        result = dict(
            fixed_prefix_bytes=100,
            previous_bytes=80,
            delta_bytes=20,
            order=list(delta_ab.ORDER),
            summary={
                "full": {"wall_median_s": 10.0},
                "strict_delta": {"wall_median_s": 2.0},
            },
            wall_reduction_pct=80.0,
            socket_rx_reduction_pct=99.0,
            logical_transfer_reduction_pct=80.0,
            trials=[{"snapshot_sha256": "secret"}],
        )
        public = delta_ab._public_summary(result)
        self.assertNotIn("trials", public)
        self.assertNotIn("snapshot_sha256", repr(public))
        self.assertEqual(
            delta_ab.ORDER,
            ("full", "strict_delta", "strict_delta",
             "full", "full", "strict_delta"))

    def test_remote_sha_cpu_parser_uses_bash_time(self):
        class Channel:
            def shutdown_write(self):
                pass
            def recv_exit_status(self):
                return 0

        class Stream(io.BytesIO):
            def __init__(self, data=b""):
                super().__init__(data)
                self.channel = Channel()

        class Client:
            def exec_command(self, command, timeout=600, get_pty=False):
                self.command = command
                return (
                    Stream(),
                    Stream((b"a" * 64) + b"  -\n"),
                    Stream(b"AKUZ_PHASE12_SHA_CPU 1.250 0.750\n"),
                )

        cfg = type("Cfg", (), {"sudo_password": ""})()
        client = Client()
        digest, _, _, server_cpu = delta_smoke._remote_prefix_sha(
            client, cfg, "'/srv/hidden.log'", 123, False)
        self.assertEqual(digest, "a" * 64)
        self.assertEqual(server_cpu, 2.0)
        self.assertIn("bash -c", client.command)
        self.assertNotIn("/usr/bin/time", client.command)

    def test_v2_ab_public_summary_redacts_trial_digests(self):
        result = dict(
            fixed_prefix_bytes=100,
            previous_bytes=80,
            delta_bytes=20,
            order=list(delta_v2_ab.ORDER),
            summary={
                "full": {"wall_median_s": 10.0},
                "single_proof_delta": {"wall_median_s": 1.0},
            },
            wall_reduction_pct=90.0,
            socket_rx_reduction_pct=99.0,
            logical_transfer_reduction_pct=80.0,
            trials=[{"snapshot_sha256": "secret"}],
        )
        public = delta_v2_ab._public_summary(result)
        self.assertNotIn("trials", public)
        self.assertNotIn("snapshot_sha256", repr(public))

    def test_post_link_temp_cleanup_failure_keeps_successful_publish(self):
        old = b"event-1\n"
        remote = old + b"event-2\n"
        real_unlink = akuz_delta.os.unlink

        def fail_part_cleanup(path):
            if Path(path).name.endswith(".part"):
                raise OSError(errno.EACCES, "synthetic cleanup denied")
            return real_unlink(path)

        with patch.object(akuz_delta.os, "unlink", side_effect=fail_part_cleanup):
            size, sha = self.run_contract(old, remote)
        self.assertEqual(size, len(remote))
        self.assertEqual(sha, digest_bytes(remote))
        self.assertEqual(self.final.read_bytes(), remote)
        self.assertEqual(self.previous.read_bytes(), old)
        # Cleanup debt is allowed only after a fully verified no-overwrite
        # publication. A later owner-scoped cleanup gate will remove it.
        self.assertTrue(self.final.with_name(self.final.name + ".part").exists())


if __name__ == "__main__":
    unittest.main()
